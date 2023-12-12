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

import json
import logging
from typing import Optional

from rich.console import Console
from rich.status import Status

from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.upgrades.base import UpgradeCoordinator, UpgradePlugins
from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.jobs.juju import run_sync
from sunbeam.versions import K8S_SERVICES, MACHINE_SERVICES

LOG = logging.getLogger(__name__)
console = Console()


class LatestInChannel(BaseStep, JujuStepHelper):
    def __init__(self, jhelper):
        """Upgrade all charms to latest in current channel.

        :jhelper: Helper for interacting with pylibjuju
        """
        super().__init__(
            "In channel upgrade", "Upgrade charms to latest revision in current channel"
        )
        self.jhelper = jhelper

    def get_charm_update(self, applications, model) -> list[str]:
        """Return a list applications that need to be refreshed."""
        candidates = []
        _status = run_sync(self.jhelper.get_model_status_full(model))
        status = json.loads(_status.to_json())
        for app_name in applications:
            if self.revision_update_needed(app_name, model, status=status):
                candidates.append(app_name)
            else:
                LOG.debug(f"{app_name} already at latest version in current channel")
        return candidates

    def get_charm_update_candidates_k8s(self) -> list[str]:
        """Return a list of all k8s charms that need to be refreshed."""
        return self.get_charm_update(K8S_SERVICES.keys(), "openstack")

    def get_charm_update_candidates_machine(self) -> list[str]:
        """Return a list of all machine charms that need to be refreshed."""
        return self.get_charm_update(MACHINE_SERVICES.keys(), "controller")

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Step can be skipped if nothing needs refreshing."""
        if (
            self.get_charm_update_candidates_k8s()
            or self.get_charm_update_candidates_machine()  # noqa
        ):
            return Result(ResultType.COMPLETED)
        else:
            return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Refresh all charms identified as needing a refresh."""
        for app_name in self.get_charm_update_candidates_k8s():
            LOG.debug(f"Refreshing {app_name}")
            app = run_sync(self.jhelper.get_application(app_name, "openstack"))
            run_sync(app.refresh())
        for app_name in self.get_charm_update_candidates_machine():
            LOG.debug(f"Refreshing {app_name}")
            app = run_sync(self.jhelper.get_application(app_name, "controller"))
            run_sync(app.refresh())
        return Result(ResultType.COMPLETED)


class LatestInChannelCoordinator(UpgradeCoordinator):
    """Coordinator for refreshing charms in their current channel."""

    def get_plan(self) -> list[BaseStep]:
        return [
            LatestInChannel(self.jhelper),
            UpgradePlugins(self.jhelper, self.tfhelper, upgrade_release=False),
        ]
