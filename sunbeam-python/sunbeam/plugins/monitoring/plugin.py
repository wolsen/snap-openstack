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

"""Monitoring plugin.

Plugin to deploy and manage monitoring, powered by COS Lite.
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.microk8s import (
    CREDENTIAL_SUFFIX,
    MICROK8S_CLOUD,
    MICROK8S_DEFAULT_STORAGECLASS,
)
from sunbeam.commands.openstack import PatchLoadBalancerServicesStep
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    run_plan,
    update_status_background,
)
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
    JujuHelper,
    JujuWaitException,
    TimeoutException,
    run_sync,
)
from sunbeam.plugins.interface.v1.base import EnableDisablePlugin

LOG = logging.getLogger(__name__)
console = Console()

COS_MODEL = "cos"
COS_DEPLOY_TIMEOUT = 1200  # 20 minutes
CONTROLLER_MODEL = CONTROLLER_MODEL.split("/")[-1]


class EnableMonitoringStep(BaseStep, JujuStepHelper):
    """Deploy monitoring stack using Terraform"""

    def __init__(
        self,
        plugin: "MonitoringPlugin",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Deploying monitoring stack", "Deploying monitoring stack")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = COS_MODEL
        self.cloud = MICROK8S_CLOUD
        self.controller_model = CONTROLLER_MODEL
        self.read_config = lambda: plugin.get_plugin_info().get("config", {})
        self.update_config = lambda c: plugin.update_plugin_info({"config": c})

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""

        config = self.read_config()
        tfvars = {
            "model": self.model,
            "cos-channel": "1.0/candidate",
            "cloud": self.cloud,
            "controller-model": self.controller_model,
            "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
            "config": {"workload-storage": MICROK8S_DEFAULT_STORAGECLASS},
        }
        config.update(tfvars)
        self.update_config(config)
        self.tfhelper.write_tfvars(tfvars)

        self.update_status(status, "deploying services")
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error deploying monitoring stack")
            return Result(ResultType.FAILED, str(e))

        apps = run_sync(self.jhelper.get_application_names(self.model))
        LOG.debug(f"Application monitored for readiness: {apps}")
        task = run_sync(update_status_background(self, apps, status))
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=COS_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug("Failed to deploy monitoring stack", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        finally:
            if not task.done():
                task.cancel()

        return Result(ResultType.COMPLETED)


class DisableMonitoringStep(BaseStep, JujuStepHelper):
    """Remove monitoring stack using Terraform"""

    def __init__(
        self,
        plugin: "MonitoringPlugin",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Removing monitoring stack", "Removing monitoring stack")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = COS_MODEL
        self.cloud = MICROK8S_CLOUD
        self.controller_model = CONTROLLER_MODEL
        self.read_config = lambda: plugin.get_plugin_info().get("config", {})
        self.update_config = lambda c: plugin.update_plugin_info({"config": c})

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""

        config = self.read_config()
        tfvars = {
            "model": self.model,
            "cos-channel": "1.0/candidate",
            "cloud": self.cloud,
            "controller-model": self.controller_model,
            "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
            "config": {"workload-storage": MICROK8S_DEFAULT_STORAGECLASS},
        }
        config.update(tfvars)
        self.update_config(config)
        self.tfhelper.write_tfvars(tfvars)
        try:
            self.tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying monitoring stack")
            return Result(ResultType.FAILED, str(e))

        try:
            run_sync(
                self.jhelper.wait_model_gone(
                    self.model,
                    timeout=COS_DEPLOY_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.debug("Failed to destroy monitoring stack", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class PatchCosLoadBalancerStep(PatchLoadBalancerServicesStep):
    SERVICES = ["traefik"]
    MODEL = COS_MODEL


class MonitoringPlugin(EnableDisablePlugin):
    version = Version("0.0.1")

    def __init__(self) -> None:
        super().__init__(name="monitoring")
        self.snap = Snap()
        self.tfplan = f"deploy-{self.name}"

    def pre_enable(self):
        src = Path(__file__).parent / "etc" / self.tfplan
        dst = self.snap.paths.user_common / "etc" / self.tfplan
        LOG.debug(f"Updating {dst} from {src}...")
        shutil.copytree(src, dst, dirs_exist_ok=True)

    def run_enable_plans(self):
        data_location = self.snap.paths.user_data
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / self.tfplan,
            plan="monitoring-plan",
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(data_location)
        plan = [
            TerraformInitStep(tfhelper),
            EnableMonitoringStep(self, tfhelper, jhelper),
            PatchCosLoadBalancerStep(),
        ]

        run_plan(plan, console)

        click.echo("Monitoring enabled.")

    def pre_disable(self):
        self.pre_enable()

    def run_disable_plans(self):
        data_location = self.snap.paths.user_data
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / self.tfplan,
            plan="monitoring-plan",
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(data_location)
        plan = [
            TerraformInitStep(tfhelper),
            DisableMonitoringStep(self, tfhelper, jhelper),
        ]

        run_plan(plan, console)
        click.echo("Monitoring disabled.")

    @click.command()
    def enable_plugin(self) -> None:
        """Enable Monitoring."""
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        """Disable  Monitoring."""
        super().disable_plugin()

    @click.group()
    def monitoring_group(self):
        """Manage Monitoring."""

    @click.command()
    def dashboard_url(self) -> None:
        """Retrieve COS Dashboard URL."""
        data_location = self.snap.paths.user_data
        jhelper = JujuHelper(data_location)

        with console.status("Retrieving dashboard URL from Grafana service ... "):
            # Retrieve config from juju actions
            model = COS_MODEL
            app = "grafana"
            action_cmd = "get-admin-password"
            unit = run_sync(jhelper.get_leader_unit(app, model))
            if not unit:
                _message = f"Unable to get {app} leader"
                raise click.ClickException(_message)

            action_result = run_sync(jhelper.run_action(unit, model, action_cmd))

            if action_result.get("return-code", 0) > 1:
                _message = "Unable to retrieve URL from Grafana service"
                raise click.ClickException(_message)

            url = action_result.get("url")
            if url:
                console.print(url)
            else:
                _message = "No URL provided by Grafana service"
                raise click.ClickException(_message)

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
                    "init": [{"name": "monitoring", "command": self.monitoring_group}],
                    "monitoring": [
                        {"name": "dashboard-url", "command": self.dashboard_url}
                    ],
                }
            )
        return commands
