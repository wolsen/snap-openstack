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

from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.commands.terraform import TerraformHelper
from sunbeam.jobs.common import BaseStep, Result, ResultType, run_plan
from sunbeam.jobs.juju import JujuHelper
from sunbeam.jobs.plugin import PluginManager

LOG = logging.getLogger(__name__)
console = Console()


class UpgradePlugins(BaseStep):
    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        tfhelper: TerraformHelper,
        upgrade_release: bool = False,
    ):
        """Upgrade plugins.

        :client: Helper for interacting with clusterd
        :jhelper: Helper for interacting with pylibjuju
        :tfhelper: Helper for interaction with Terraform
        :upgrade_release: Whether to upgrade channel
        """
        super().__init__("Validation", "Running pre-upgrade validation")
        self.client = client
        self.jhelper = jhelper
        self.tfhelper = tfhelper
        self.upgrade_release = upgrade_release

    def run(self, status: Optional[Status] = None) -> Result:
        PluginManager.update_plugins(
            self.client, repos=["core"], upgrade_release=self.upgrade_release
        )
        return Result(ResultType.COMPLETED)


class UpgradeCoordinator:
    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        tfhelper: TerraformHelper,
        channel: str | None = None,
    ):
        """Upgrade coordinator.

        Execute plan for conducting an upgrade.

        :client: Helper for interacting with clusterd
        :jhelper: Helper for interacting with pylibjuju
        :tfhelper: Helper for interaction with Terraform
        :channel: OpenStack channel to upgrade charms to
        """
        self.client = client
        self.channel = channel
        self.jhelper = jhelper
        self.tfhelper = tfhelper

    def get_plan(self) -> list[BaseStep]:
        """Return the plan for this upgrade.

        Return the steps to complete this upgrade.
        """
        return []

    def run_plan(self) -> None:
        """Execute the upgrade plan."""
        plan = self.get_plan()
        run_plan(plan, console)
