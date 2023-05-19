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

import ast
import logging
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.commands.terraform import TerraformException, TerraformHelper
from sunbeam.jobs import questions
from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.jobs.juju import (
    MODEL,
    ActionFailedException,
    ApplicationNotFoundException,
    JujuHelper,
    TimeoutException,
    UnitNotFoundException,
    run_sync,
)

LOG = logging.getLogger(__name__)
APPLICATION = "microceph"
MICROCEPH_APP_TIMEOUT = 180  # 3 minutes, managing the application should be fast
MICROCEPH_UNIT_TIMEOUT = (
    1200  # 15 minutes, adding / removing units can take a long time
)
OSD_PATH_PREFIX = "/dev/disk/by-id/"


class DeployMicrocephApplicationStep(BaseStep):
    """Deploy Microceph application using Terraform"""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Deploy MicroCeph", "Deploying MicroCeph")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.client = Client()

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

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to deploy microceph"""
        machine_ids = []
        try:
            application = run_sync(self.jhelper.get_application(APPLICATION, MODEL))
            machine_ids.extend(unit.machine.id for unit in application.units)
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))

        self.tfhelper.write_tfvars({"machine_ids": machine_ids})
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        try:
            run_sync(
                self.jhelper.wait_application_ready(
                    APPLICATION,
                    MODEL,
                    accepted_status=["active", "unknown"],
                    timeout=MICROCEPH_APP_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddMicrocephUnitStep(BaseStep):
    def __init__(self, name: str, jhelper: JujuHelper):
        super().__init__("Add MicroCeph unit", "Adding MicroCeph unit to machine")

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
            return Result(ResultType.FAILED, "Microceph has not been deployed")

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

    def run(self, status: Optional[Status] = None) -> Result:
        """Add unit to microceph application on Juju model."""
        try:
            unit = run_sync(
                self.jhelper.add_unit(APPLICATION, MODEL, str(self.machine_id))
            )
            run_sync(
                self.jhelper.wait_unit_ready(
                    unit.name, MODEL, timeout=MICROCEPH_UNIT_TIMEOUT
                )
            )
        except (ApplicationNotFoundException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveMicrocephUnitStep(BaseStep):
    def __init__(self, name: str, jhelper: JujuHelper):
        super().__init__(
            "Remove MicroCeph unit", "Removing MicroCeph unit from machine"
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
            return Result(ResultType.SKIPPED, "MicroCeph has not been deployed yet")

        for unit in application.units:
            if unit.machine.id == machine_id:
                LOG.debug(f"Unit {unit.name} is deployed on machine: {machine_id}")
                self.unit = unit.name
                return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Remove unit from microk8s application on Juju model."""
        try:
            run_sync(self.jhelper.remove_unit(APPLICATION, str(self.unit), MODEL))
            run_sync(
                self.jhelper.wait_application_ready(
                    APPLICATION,
                    MODEL,
                    accepted_status=["active", "unknown"],
                    timeout=MICROCEPH_UNIT_TIMEOUT,
                )
            )
        except (ApplicationNotFoundException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class ConfigureMicrocephOSDStep(BaseStep):
    """Configure Microceph OSD disks"""

    _CONFIG = "TerraformVarsMicroceph"

    def __init__(
        self,
        name: str,
        jhelper: JujuHelper,
        preseed_file: Optional[Path] = None,
        accept_defaults: bool = False,
    ):
        super().__init__("Configure MicroCeph storage", "Configuring MicroCeph storage")
        self.name = name
        self.jhelper = jhelper
        self.preseed_file = preseed_file
        self.accept_defaults = accept_defaults
        self.client = Client()
        self.variables = {}
        self.machine_id = ""
        self.disks = ""

    def microceph_config_questions(self):
        disks = self.get_unpartitioned_disks()

        disks_str = None
        first_disk = None
        if len(disks) > 0:
            disks_str = ",".join(disks)
            first_disk = disks[0]

        return {
            "osd_devices": questions.PromptQuestion(
                f"Disks to attach to microceph, available - {disks_str}",
                default_value=first_disk,
            ),
        }

    def get_unpartitioned_disks(self) -> list:
        unpartitioned_disks = []

        try:
            node = self.client.cluster.get_node_info(self.name)
            self.machine_id = str(node.get("machineid"))
            unit = run_sync(
                self.jhelper.get_unit_from_machine(APPLICATION, self.machine_id, MODEL)
            )
            LOG.debug(f"Running action list-disks on {unit.entity_id}")
            action_result = run_sync(
                self.jhelper.run_action(unit.entity_id, MODEL, "list-disks")
            )
            LOG.debug(f"Result after running action list-disks: {action_result}")

            disks = ast.literal_eval(action_result.get("unpartitioned-disks", "[]"))
            unpartitioned_disks = [disk.get("path") for disk in disks]
            # Remove duplicates if any
            unpartitioned_disks = list(set(unpartitioned_disks))
            if OSD_PATH_PREFIX in unpartitioned_disks:
                unpartitioned_disks.remove(OSD_PATH_PREFIX)

        except (UnitNotFoundException, ActionFailedException) as e:
            LOG.debug(str(e))
            raise click.ClickException("Unable to list disks")

        LOG.debug(f"Unparitioned disks: {unpartitioned_disks}")
        return unpartitioned_disks

    def prompt(self, console: Optional[Console] = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        self.variables = questions.load_answers(self.client, self._CONFIG)
        self.variables.setdefault("microceph_config", {})
        self.variables["microceph_config"].setdefault(self.name, {"osd_devices": ""})

        if self.preseed_file:
            preseed = questions.read_preseed(self.preseed_file)
        else:
            preseed = {}
        # Set defaults
        preseed.setdefault("microceph_config", {})
        preseed["microceph_config"].setdefault(self.name, {"osd_devices": ""})

        microceph_config_bank = questions.QuestionBank(
            questions=self.microceph_config_questions(),
            console=console,  # type: ignore
            preseed=preseed.get("microceph_config").get(self.name),
            previous_answers=self.variables.get("microceph_config").get(self.name),
            accept_defaults=self.accept_defaults,
        )
        # Microceph configuration
        self.disks = microceph_config_bank.osd_devices.ask()
        self.variables["microceph_config"][self.name]["osd_devices"] = self.disks

        LOG.debug(self.variables)
        questions.write_answers(self.client, self._CONFIG, self.variables)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return True

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if not self.disks:
            LOG.debug(
                "Skipping ConfigureMicrocephOSDStep as no osd devices are selected"
            )
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Configure local disks on microceph."""
        try:
            unit = run_sync(
                self.jhelper.get_unit_from_machine(APPLICATION, self.machine_id, MODEL)
            )
            LOG.debug(f"Running action add-osd on {unit.entity_id}")
            action_result = run_sync(
                self.jhelper.run_action(
                    unit.entity_id,
                    MODEL,
                    "add-osd",
                    action_params={
                        "device-id": self.disks,
                    },
                )
            )
            LOG.debug(f"Result after running action add-osd: {action_result}")
        except (UnitNotFoundException, ActionFailedException) as e:
            LOG.debug(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
