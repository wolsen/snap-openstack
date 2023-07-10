# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import inspect
import logging
import shutil
from abc import abstractmethod
from enum import Enum
from pathlib import Path
from typing import Optional

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.openstack import (
    OPENSTACK_MODEL,
    determine_target_topology_at_bootstrap,
)
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.jobs.checks import VerifyBootstrappedCheck
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    read_config,
    run_plan,
    run_preflight_checks,
    update_config,
)
from sunbeam.jobs.juju import JujuHelper, JujuWaitException, TimeoutException, run_sync
from sunbeam.plugins.interface.v1.base import EnableDisablePlugin

LOG = logging.getLogger(__name__)
console = Console()

APPLICATION_DEPLOY_TIMEOUT = 1200  # 15 minutes


class TERRAFORM_PLAN_LOCATION(Enum):
    SUNBEAM_TERRAFORM_REPO = 1
    PLUGIN_REPO = 2


class OpenStackControlPlanePlugin(EnableDisablePlugin):
    interface_version = Version("0.0.1")

    def __init__(self, name: str, tf_plan_location: TERRAFORM_PLAN_LOCATION) -> None:
        super().__init__(name=name)
        self.app_name = self.name.capitalize()
        self.tf_plan_location = tf_plan_location
        if self.tf_plan_location == TERRAFORM_PLAN_LOCATION.SUNBEAM_TERRAFORM_REPO:
            self.tfplan = "openstack"
        else:
            self.tfplan = self.name

        self.snap = Snap()

    def _get_tf_plan_full_path(self):
        if self.tf_plan_location == TERRAFORM_PLAN_LOCATION.SUNBEAM_TERRAFORM_REPO:
            return self.snap.paths.snap / "etc" / f"deploy-{self.tfplan}"
        else:
            plugin_class_dir = Path(inspect.getfile(self.__class__)).parent
            return plugin_class_dir / "etc" / f"deploy-{self.tfplan}"

    def _get_plan_name(self):
        return f"{self.tfplan}-plan"

    def is_openstack_control_plane(self):
        """Is plugin deploys openstack control plane."""
        return True

    def pre_enable(self):
        preflight_checks = []
        preflight_checks.append(VerifyBootstrappedCheck())
        run_preflight_checks(preflight_checks, console)
        src = self._get_tf_plan_full_path()
        dst = self.snap.paths.user_common / "etc" / f"deploy-{self.tfplan}"
        LOG.debug(f"Updating {dst} from {src}...")
        shutil.copytree(src, dst, dirs_exist_ok=True)

    def run_enable_plans(self):
        data_location = self.snap.paths.user_data
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / f"deploy-{self.tfplan}",
            plan=self._get_plan_name(),
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(data_location)
        plan = [
            TerraformInitStep(tfhelper),
            EnableOpenStackApplicationStep(tfhelper, jhelper, self),
        ]

        run_plan(plan, console)
        click.echo(f"OpenStack {self.name} application enabled.")

    def pre_disable(self):
        self.pre_enable()

    def run_disable_plans(self):
        data_location = self.snap.paths.user_data
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / f"deploy-{self.tfplan}",
            plan=self._get_plan_name(),
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(data_location)
        plan = [
            TerraformInitStep(tfhelper),
            DisableOpenStackApplicationStep(tfhelper, jhelper, self),
        ]

        run_plan(plan, console)
        click.echo(f"OpenStack {self.name} application disabled.")

    def get_tfvar_config_key(self):
        if self.tf_plan_location == TERRAFORM_PLAN_LOCATION.SUNBEAM_TERRAFORM_REPO:
            return "TerraformVarsOpenstack"
        else:
            return f"TerraformVars{self.app_name}"

    def get_database_topology(self) -> str:
        # Database topology can be set only during bootstrap and cannot be changed.
        return determine_target_topology_at_bootstrap()

    def set_application_timeout_on_enable(self) -> str:
        """Set Application Timeout on enabling the plugin.

        The plugin plan will timeout if the applications
        are not in active status within in this time.
        """
        return APPLICATION_DEPLOY_TIMEOUT

    def set_application_timeout_on_disable(self) -> str:
        """Set Application Timeout on disabling the plugin.

        The plugin plan will timeout if the applications
        are not removed within this time.
        """
        return APPLICATION_DEPLOY_TIMEOUT

    @abstractmethod
    def set_application_names(self) -> list:
        """Application names handled by the terraform plan.

        Returns list of applications that are deployed by the
        terraform plan during enable. During disable, these
        applications should get destroyed.
        """

    @abstractmethod
    def set_tfvars_on_enable(self) -> dict:
        """Set terraform variables to enable the application."""

    @abstractmethod
    def set_tfvars_on_disable(self) -> dict:
        """Set terraform variables to disable the application."""

    @abstractmethod
    def set_tfvars_on_resize(self) -> dict:
        """Set terraform variables to resize the application."""

    @abstractmethod
    def enable_plugin(self) -> None:
        super().enable_plugin()

    @abstractmethod
    def disable_plugin(self) -> None:
        super().disable_plugin()


class EnableOpenStackApplicationStep(BaseStep, JujuStepHelper):
    """Enable OpenStack Heat application using Terraform"""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        plugin: OpenStackControlPlanePlugin,
    ):
        super().__init__(
            f"Enable OpenStack {plugin.name}",
            f"Enabling OpenStack {plugin.name} application",
        )
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.plugin = plugin
        self.model = OPENSTACK_MODEL
        self.client = Client()

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to deploy openstack application"""
        config_key = self.plugin.get_tfvar_config_key()

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        tfvars.update(self.plugin.set_tfvars_on_enable())
        update_config(self.client, config_key, tfvars)
        self.tfhelper.write_tfvars(tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        apps = self.plugin.set_application_names()
        LOG.debug(f"Application monitored for readiness: {apps}")
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=APPLICATION_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DisableOpenStackApplicationStep(BaseStep, JujuStepHelper):
    """Disable OpenStack Heat application using Terraform"""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        plugin: OpenStackControlPlanePlugin,
    ):
        super().__init__(
            f"Disable OpenStack {plugin.name}",
            f"Disabling OpenStack {plugin.name} application",
        )
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.plugin = plugin
        self.model = OPENSTACK_MODEL
        self.client = Client()

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to remove openstack application"""
        config_key = self.plugin.get_tfvar_config_key()

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        tfvars.update(self.plugin.set_tfvars_on_disable())
        update_config(self.client, config_key, tfvars)
        self.tfhelper.write_tfvars(tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        apps = self.plugin.set_application_names()
        LOG.debug(f"Application monitored for readiness: {apps}")
        # TODO(hemanth): Check if apps are removed or not.

        return Result(ResultType.COMPLETED)
