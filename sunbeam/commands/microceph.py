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
from typing import Optional

from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.commands.terraform import TerraformException, TerraformHelper
from sunbeam.jobs import questions
from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.jobs.juju import (
    MODEL,
    ApplicationNotFoundException,
    JujuHelper,
    TimeoutException,
    run_sync,
)

LOG = logging.getLogger(__name__)
APPLICATION = "microceph"
MICROCEPH_APP_TIMEOUT = 180  # 3 minutes, managing the application should be fast
MICROCEPH_UNIT_TIMEOUT = (
    1200  # 15 minutes, adding / removing units can take a long time
)


def microceph_config_questions():
    return {
        "microceph_osd_devices": questions.PromptQuestion(
            "Disks to attach to microceph"
        ),
    }


class DeployMicrocephApplicationStep(BaseStep):
    """Deploy Microceph application using Terraform"""

    _CONFIG = "TerraformVarsMicroceph"

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        preseed_file: Optional[Path] = None,
        accept_defaults: bool = False,
    ):
        super().__init__("Deploy MicroCeph", "Deploying MicroCeph")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.preseed_file = preseed_file
        self.accept_defaults = accept_defaults
        self.client = Client()
        self.answer_file = self.tfhelper.path / "config.tfvars.json"
        self.variables = {}

    def prompt(self, console: Optional[Console] = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        self.variables = questions.load_answers(self.client, self._CONFIG)
        self.variables.setdefault("microceph_osd_devices", "")

        if self.preseed_file:
            preseed = questions.read_preseed(self.preseed_file)
        else:
            preseed = {}
        microceph_config_bank = questions.QuestionBank(
            questions=microceph_config_questions(),
            console=console,  # type: ignore
            preseed=preseed.get("microceph_config"),
            previous_answers=self.variables,
            accept_defaults=self.accept_defaults,
        )
        # Microceph configuration
        self.variables[
            "microceph_osd_devices"
        ] = microceph_config_bank.microceph_osd_devices.ask()

        LOG.debug(self.variables)
        questions.write_answers(self.client, self._CONFIG, self.variables)
        # Write answers to terraform location as a separate variables file
        self.tfhelper.write_tfvars(self.variables, self.answer_file)

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
        return Result(ResultType.COMPLETED)

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
        super().__init__("Add Microceph unit", "Adding Microceph unit to machine")

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
                    APPLICATION, MODEL, timeout=MICROCEPH_UNIT_TIMEOUT
                )
            )
        except (ApplicationNotFoundException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
