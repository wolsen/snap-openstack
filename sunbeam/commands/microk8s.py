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
import operator
from pathlib import Path
import subprocess
from typing import Optional

from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
)
from sunbeam.commands.juju import (
    CONTROLLER_MODEL,
    JujuStepHelper,
)
from sunbeam.jobs import questions
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
)


LOG = logging.getLogger(__name__)
APPLICATION = "microk8s"


def microk8s_addons_questions():
    return {
        "dns": questions.PromptQuestion(
            "Nameserver addresses", default_value="8.8.8.8,8.8.4.4"
        ),
        "metallb": questions.PromptQuestion(
            "Metallb range", default_value="10.20.21.10-10.20.21.20"
        ),
    }


class DeployMicrok8sApplicationStep(BaseStep, JujuStepHelper):
    """Deploy Microk8s application using Terraform cloud"""

    _CONFIG = "TerraformVarsMicrok8sAddons"

    def __init__(
        self,
        tfhelper: TerraformHelper,
        preseed_file: Optional[Path] = None,
        accept_defaults: bool = False,
    ):
        super().__init__("Deploy MicroK8S", "Deploy MicroK8S application onto cloud")
        self.tfhelper = tfhelper
        self.preseed_file = preseed_file
        self.accept_defaults = accept_defaults
        self.client = Client()
        self.answer_file = self.tfhelper.path / "addons.auto.tfvars.json"
        self.variables = {}

    def prompt(self, console: Optional[Console] = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        self.variables = questions.load_answers(self.client, self._CONFIG)
        self.variables.setdefault("addons", {})

        if self.preseed_file:
            preseed = questions.read_preseed(self.preseed_file)
        else:
            preseed = {}
        microk8s_addons_bank = questions.QuestionBank(
            questions=microk8s_addons_questions(),
            console=console,  # type: ignore
            preseed=preseed.get("addons"),
            previous_answers=self.variables.get("addons", {}),
            accept_defaults=self.accept_defaults,
        )
        # Microk8s configuration
        self.variables["addons"]["dns"] = microk8s_addons_bank.dns.ask()
        self.variables["addons"]["metallb"] = microk8s_addons_bank.metallb.ask()
        self.variables["addons"]["hostpath-storage"] = ""

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
        """Apply terraform configuration to deploy microk8s"""
        application = self._juju_cmd("status", APPLICATION, "-m", CONTROLLER_MODEL)[
            "applications"
        ].get(APPLICATION)
        if application is None:
            machine_ids = []
        else:
            machine_ids = list(
                map(
                    operator.itemgetter("machine"),
                    application.get("units", {}).values(),
                )
            )
        self.tfhelper.write_tfvars({"machine_ids": machine_ids})
        try:
            self.tfhelper.apply()
            # Note(gboutry): application is in state unknown when it's deployed
            # without units
            self.wait_application_ready(
                APPLICATION, accepted_status=["active", "unknown"]
            )
            return Result(ResultType.COMPLETED)
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))


class AddMicrok8sUnitStep(BaseStep, JujuStepHelper):
    def __init__(self, name: str):
        super().__init__("Add MicroK8S unit", "Add MicroK8S unit on machine")

        self.name = name
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

        application = self._juju_cmd("status", APPLICATION, "-m", CONTROLLER_MODEL)[
            "applications"
        ].get(APPLICATION)
        if application is None:
            return Result(
                ResultType.FAILED, "MicroK8S application has not been deployed yet"
            )
        for name, unit in application.get("units", {}).items():
            if unit["machine"] == self.machine_id:
                LOG.debug(
                    f"Unit {name} is already deployed on machine: {self.machine_id}"
                )
                return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Add unit to microk8s application on Juju model."""

        cmd = [
            self._get_juju_binary(),
            "add-unit",
            APPLICATION,
            "--to",
            str(self.machine_id),
            "-m",
            CONTROLLER_MODEL,
        ]
        try:
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
            self.wait_application_ready(APPLICATION)
            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception(
                f"Error adding unit on machine {self.machine_id} in Juju model"
            )
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))


class RemoveMicrok8sUnitStep(BaseStep, JujuStepHelper):
    def __init__(self, name: str):
        super().__init__("Remove MicroK8S unit", "Remove MicroK8S unit from machine")

        self.name = name
        self.machine_id = ""
        self.unit_name = None

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

        application = self._juju_cmd("status", APPLICATION, "-m", CONTROLLER_MODEL)[
            "applications"
        ].get(APPLICATION)
        if application is None:
            return Result(
                ResultType.SKIPPED, "MicroK8S application has not been deployed yet"
            )

        for name, unit in application.get("units", {}).items():
            if unit["machine"] == machine_id:
                LOG.debug(f"Unit {name} is deployed on machine: {machine_id}")
                self.unit_name = name
                return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Remove unit from microk8s application on Juju model."""

        cmd = [
            self._get_juju_binary(),
            "remove-unit",
            str(self.unit_name),
            "-m",
            CONTROLLER_MODEL,
        ]
        try:
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
            self.wait_application_ready(APPLICATION)
            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception(
                f"Error removing unit {self.unit_name}"
                " on machine {self.machine_id} in Juju model"
            )
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))
