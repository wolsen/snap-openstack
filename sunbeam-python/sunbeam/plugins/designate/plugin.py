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
from typing import Optional

import click
from packaging.version import Version
from rich.console import Console

from sunbeam.commands.openstack import PatchLoadBalancerServicesStep
from sunbeam.commands.terraform import TerraformHelper, TerraformInitStep
from sunbeam.jobs.common import run_plan
from sunbeam.jobs.juju import JujuHelper
from sunbeam.plugins.interface.v1.openstack import (
    OpenStackControlPlanePlugin,
    EnableOpenStackApplicationStep,
    TerraformPlanLocation,
)

LOG = logging.getLogger(__name__)
console = Console()


class PatchBind9LoadBalancerStep(PatchLoadBalancerServicesStep):
    SERVICES = ["bind9"]


class DesignatePlugin(OpenStackControlPlanePlugin):
    version = Version("0.0.1")
    nameservers: Optional[str]

    def __init__(self) -> None:
        super().__init__(
            name="designate",
            tf_plan_location=TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO,
        )
        self.nameservers = None

    def run_enable_plans(self) -> None:
        """Run plans to enable plugin."""
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
            PatchBind9LoadBalancerStep(),
        ]

        run_plan(plan, console)
        click.echo(f"OpenStack {self.name!r} application enabled.")

    def set_application_names(self) -> list:
        """Application names handled by the terraform plan."""
        database_topology = self.get_database_topology()

        apps = ["bind9", "designate", "designate-mysql-router"]
        if database_topology == "multi":
            apps.append("designate-mysql")

        return apps

    def set_tfvars_on_enable(self) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "designate-channel": "latest/edge",
            "enable-designate": True,
            "nameservers": self.nameservers,
        }

    def set_tfvars_on_disable(self) -> dict:
        """Set terraform variables to disable the application."""
        return {"enable-designate": False}

    def set_tfvars_on_resize(self) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    @click.command()
    @click.option(
        "--nameservers",
        required=True,
        help="""
        Space delimited list of nameservers. These are the nameservers that
        have been provided to the domain registrar in order to delegate
        the domain to Designate.  e.g. "ns1.example.com. ns2.example.com."
        """,
    )
    def enable_plugin(self, nameservers: str) -> None:
        """Enable OpenStack Designate application."""
        self.nameservers = nameservers
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        """Disable OpenStack Designate applications."""
        super().disable_plugin()
