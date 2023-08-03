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

"""Observability Stack plugin.

Plugin to deploy and manage Observability stack COS Lite.
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
from sunbeam.jobs.juju import JujuHelper, JujuWaitException, TimeoutException, run_sync
from sunbeam.plugins.interface.v1.base import EnableDisablePlugin

LOG = logging.getLogger(__name__)
console = Console()

COS_MODEL = "cos"
COS_DEPLOY_TIMEOUT = 1200  # 20 minutes


class EnableCosStep(BaseStep, JujuStepHelper):
    """Deploy COS using Terraform"""

    def __init__(
        self,
        plugin: "CosPlugin",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Deploying COS", "Deploying COS")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = COS_MODEL
        self.cloud = MICROK8S_CLOUD
        self.read_config = lambda: plugin.get_plugin_info().get("config", {})
        self.update_config = lambda c: plugin.update_plugin_info({"config": c})

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""

        config = self.read_config()
        tfvars = {
            "model": self.model,
            "cos-channel": "1.0/candidate",
            "cloud": self.cloud,
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
            LOG.exception("Error deploying COS")
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
            LOG.debug("Failed to deploy cos", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        finally:
            if not task.done():
                task.cancel()

        return Result(ResultType.COMPLETED)


class DisableCosStep(BaseStep, JujuStepHelper):
    """Remove COS using Terraform"""

    def __init__(
        self,
        plugin: "CosPlugin",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Removing COS", "Removing COS")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = COS_MODEL
        self.cloud = MICROK8S_CLOUD
        self.read_config = lambda: plugin.get_plugin_info().get("config", {})
        self.update_config = lambda c: plugin.update_plugin_info({"config": c})

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""

        config = self.read_config()
        tfvars = {
            "model": self.model,
            "cos-channel": "1.0/candidate",
            "cloud": self.cloud,
            "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
            "config": {"workload-storage": MICROK8S_DEFAULT_STORAGECLASS},
        }
        config.update(tfvars)
        self.update_config(config)
        self.tfhelper.write_tfvars(tfvars)
        try:
            self.tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying cos")
            return Result(ResultType.FAILED, str(e))

        try:
            run_sync(
                self.jhelper.wait_model_gone(
                    self.model,
                    timeout=COS_DEPLOY_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.debug("Failed to destroy cos", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class PatchCosLoadBalancerStep(PatchLoadBalancerServicesStep):
    SERVICES = ["traefik"]
    MODEL = COS_MODEL


class CosPlugin(EnableDisablePlugin):
    version = Version("0.0.1")

    def __init__(self) -> None:
        super().__init__(name="cos")
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
            plan="cos-plan",
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(data_location)
        plan = [
            TerraformInitStep(tfhelper),
            EnableCosStep(self, tfhelper, jhelper),
            PatchCosLoadBalancerStep(),
        ]

        run_plan(plan, console)

        click.echo("Observability Stack enabled.")

    def pre_disable(self):
        self.pre_enable()

    def run_disable_plans(self):
        data_location = self.snap.paths.user_data
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / self.tfplan,
            plan="cos-plan",
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(data_location)
        plan = [
            TerraformInitStep(tfhelper),
            DisableCosStep(self, tfhelper, jhelper),
        ]

        run_plan(plan, console)
        click.echo("Observability Stack disabled.")

    @click.command()
    def enable_plugin(self) -> None:
        """Enable  Observability Stack."""
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        """Disable  Observability Stack."""
        super().disable_plugin()
