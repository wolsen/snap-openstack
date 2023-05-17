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
from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.commands.terraform import TerraformException, TerraformHelper
from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
    MODEL,
    ApplicationNotFoundException,
    JujuHelper,
    TimeoutException,
    run_sync,
)

LOG = logging.getLogger(__name__)
APPLICATION = "openstack-hypervisor"
HYPERVISOR_APP_TIMEOUT = 180  # 3 minutes, managing the application should be fast
HYPERVISOR_UNIT_TIMEOUT = (
    1200  # 15 minutes, adding / removing units can take a long time
)


class DeployHypervisorApplicationStep(BaseStep, JujuStepHelper):
    """Deploy openstack-hyervisor application using Terraform cloud"""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Deploy openstack-hypervisor",
            "Deploy openstack-hypervisor application onto cloud",
        )
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.client = Client()
        self.hypervisor_model = CONTROLLER_MODEL.split("/")[-1]
        self.openstack_model = OPENSTACK_MODEL

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            run_sync(self.jhelper.get_application(APPLICATION, MODEL))
        except ApplicationNotFoundException:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(
        self, status: Optional[Status] = None, console: Optional[Console] = None
    ) -> Result:
        """Apply terraform configuration to deploy hypervisor"""
        machine_ids = []
        try:
            application = run_sync(self.jhelper.get_application(APPLICATION, MODEL))
            machine_ids.extend(unit.machine.id for unit in application.units)
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))

        self.tfhelper.write_tfvars(
            {
                "hypervisor_model": self.hypervisor_model,
                "openstack_model": self.openstack_model,
                "machine_ids": machine_ids,
            }
        )
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        # Note(gboutry): application is in state unknown when it's deployed
        # without units
        try:
            run_sync(
                self.jhelper.wait_application_ready(
                    APPLICATION,
                    MODEL,
                    accepted_status=["active", "unknown"],
                    timeout=HYPERVISOR_APP_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddHypervisorUnitStep(BaseStep, JujuStepHelper):
    def __init__(self, name: str, jhelper: JujuHelper):
        super().__init__("Add Hypervisor unit", "Add Hypervisor unit on machine")

        self.name = name
        self.jhelper = jhelper
        self.machine_id = ""

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        client = Client()
        try:
            node = client.cluster.get_node_info(self.name)
            self.machine_id = str(node.get("machineid"))
        except NodeNotExistInClusterException as e:
            return Result(ResultType.FAILED, str(e))

        try:
            application = run_sync(self.jhelper.get_application(APPLICATION, MODEL))
        except ApplicationNotFoundException:
            return Result(
                ResultType.FAILED,
                "openstack-hypervisor application has not been deployed yet",
            )

        for unit in application.units:
            if unit.machine.id == self.machine_id:
                LOG.debug(
                    (
                        f"Unit {unit.name} is already deployed"
                        f" on machine: {self.machine_id}"
                    )
                )
                return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(
        self, status: Optional[Status] = None, console: Optional[Console] = None
    ) -> Result:
        """Add unit to openstack-hypervisor application on Juju model."""
        try:
            unit = run_sync(
                self.jhelper.add_unit(APPLICATION, MODEL, str(self.machine_id))
            )
            run_sync(
                self.jhelper.wait_unit_ready(
                    unit.name, MODEL, timeout=HYPERVISOR_UNIT_TIMEOUT
                )
            )
        except (ApplicationNotFoundException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveHypervisorUnitStep(BaseStep, JujuStepHelper):
    def __init__(self, name: str, jhelper: JujuHelper):
        super().__init__(
            "Remove openstack-hypervisor unit",
            "Remove openstack-hypervisor unit from machine",
        )

        self.name = name
        self.jhelper = jhelper
        self.unit = None

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        client = Client()
        try:
            node = client.cluster.get_node_info(self.name)
            machine_id = str(node.get("machineid"))
        except NodeNotExistInClusterException:
            LOG.debug(f"Machine {self.name} does not exist, skipping.")
            return Result(ResultType.SKIPPED)

        try:
            application = run_sync(self.jhelper.get_application(APPLICATION, MODEL))
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))
            return Result(
                ResultType.SKIPPED, "MicroK8S application has not been deployed yet"
            )

        for unit in application.units:
            if unit.machine.id == machine_id:
                LOG.debug(f"Unit {unit.name} is deployed on machine: {machine_id}")
                self.unit = unit.name
                return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(
        self, status: Optional[Status] = None, console: Optional[Console] = None
    ) -> Result:
        """Remove unit from openstack-hypervisor application on Juju model."""
        try:
            run_sync(self.jhelper.remove_unit(APPLICATION, str(self.unit), MODEL))
            run_sync(
                self.jhelper.wait_application_ready(
                    APPLICATION,
                    MODEL,
                    accepted_status=["active", "unknown"],
                    timeout=HYPERVISOR_UNIT_TIMEOUT,
                )
            )
        except (ApplicationNotFoundException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
