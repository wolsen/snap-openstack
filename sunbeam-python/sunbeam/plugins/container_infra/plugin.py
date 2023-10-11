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

import logging
from pathlib import Path
import shutil
from typing import Optional

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    read_config,
    run_plan,
)
from sunbeam.jobs.juju import JujuHelper
from sunbeam.plugins.interface.v1.openstack import (
    OpenStackControlPlanePlugin,
    TerraformPlanLocation,
)
from sunbeam.plugins.orchestration.plugin import OrchestrationPlugin
from sunbeam.plugins.secrets.plugin import SecretsPlugin

LOG = logging.getLogger(__name__)
console = Console()


class ContainerInfraConfigureStep(BaseStep):
    """Configure Container Infra service."""

    def __init__(
        self,
        tfhelper: TerraformHelper,
    ):
        super().__init__(
            "Configure Container Infra",
            "Configure Cloud for Container Infra use",
        )
        self.tfhelper = tfhelper

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error configuring Container Infra plugin.")
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class ContainerInfraPlugin(OpenStackControlPlanePlugin):
    version = Version("0.0.1")

    def __init__(self) -> None:
        super().__init__(
            name="container-infra",
            tf_plan_location=TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO,
        )
        self.configure_plan = "container-infra-setup"

    def set_application_names(self) -> list:
        """Application names handled by the terraform plan."""
        apps = ["magnum", "magnum-mysql-router"]
        if self.get_database_topology() == "multi":
            apps.extend(["magnum-mysql"])

        return apps

    def set_tfvars_on_enable(self) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "magnum-channel": "2023.1/edge",
            "enable-magnum": True,
        }

    def set_tfvars_on_disable(self) -> dict:
        """Set terraform variables to disable the application."""
        return {"enable-magnum": False}

    def set_tfvars_on_resize(self) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def pre_enable(self) -> None:
        """Check required plugins are enabled."""
        super().pre_enable()
        # TODO(gboutry): Remove this when plugin dependency is implemented
        try:
            secrets_info = read_config(self.client, SecretsPlugin().plugin_key)
            enabled = secrets_info.get("enabled", False)
            if enabled == "false":
                raise ValueError("Secrets plugin is not enabled")
        except (ConfigItemNotFoundException, ValueError) as e:
            raise click.ClickException(
                "OpenStack Container Infra plugin requires Secrets plugin to be enabled"
            ) from e
        try:
            orchestration_info = read_config(
                self.client, OrchestrationPlugin().plugin_key
            )
            enabled = orchestration_info.get("enabled", False)
            if enabled == "false":
                raise ValueError("Orchestration plugin is not enabled")
        except (ConfigItemNotFoundException, ValueError) as e:
            raise click.ClickException(
                "OpenStack Container Infra plugin requires Orchestration"
                " plugin to be enabled"
            ) from e

        src = Path(__file__).parent / "etc" / self.configure_plan
        dst = self.snap.paths.user_common / "etc" / self.configure_plan
        LOG.debug(f"Updating {dst} from {src}...")
        shutil.copytree(src, dst, dirs_exist_ok=True)

    @click.command()
    def enable_plugin(self) -> None:
        """Enable Container Infra service."""
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        """Disable Container Infra service."""
        super().disable_plugin()

    @click.group()
    def container_infra(self) -> None:
        """Manage Container Infra."""

    @click.command()
    def configure(self):
        """Configure Cloud for Container Infra use."""
        data_location = self.snap.paths.user_data

        jhelper = JujuHelper(data_location)
        admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / self.configure_plan,
            env=admin_credentials,
            plan="container-infra-plan",
            backend="http",
            data_location=data_location,
        )
        plan = [
            TerraformInitStep(tfhelper),
            ContainerInfraConfigureStep(tfhelper),
        ]

        run_plan(plan, console)

    def commands(self) -> dict:
        """Dict of clickgroup along with commands."""
        commands = super().commands()
        try:
            enabled = self.enabled
        except ClusterServiceUnavailableException:
            LOG.debug(
                "Failed to query for plugin status, is cloud bootstrapped ?",
                exc_info=True,
            )
            enabled = False

        if enabled:
            commands.update(
                {
                    "init": [
                        {"name": "container-infra", "command": self.container_infra}
                    ],
                    "container-infra": [
                        {"name": "configure", "command": self.configure}
                    ],
                }
            )
        return commands
