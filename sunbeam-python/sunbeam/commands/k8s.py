# Copyright (c) 2024 Canonical Ltd.
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

import ipaddress
import logging

import yaml
from rich.console import Console

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    Status,
    read_config,
    update_config,
)
from sunbeam.jobs.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuHelper,
    LeaderNotFoundException,
    UnsupportedKubeconfigException,
    run_sync,
)
from sunbeam.jobs.manifest import Manifest
from sunbeam.jobs.questions import (
    PromptQuestion,
    QuestionBank,
    load_answers,
    write_answers,
)
from sunbeam.jobs.steps import (
    AddMachineUnitsStep,
    DeployMachineApplicationStep,
    RemoveMachineUnitStep,
)

LOG = logging.getLogger(__name__)
K8S_CLOUD = "sunbeam-k8s"
K8S_DEFAULT_STORAGECLASS = "csi-rawfile-default"
K8S_CONFIG_KEY = "TerraformVarsK8S"
K8S_ADDONS_CONFIG_KEY = "TerraformVarsK8SAddons"
K8S_KUBECONFIG_KEY = "K8SKubeConfig"
APPLICATION = "k8s"
K8S_APP_TIMEOUT = 180  # 3 minutes, managing the application should be fast
K8S_UNIT_TIMEOUT = 1200  # 20 minutes, adding / removing units can take a long time
K8S_ENABLE_ADDONS_TIMEOUT = 180  # 3 minutes
CREDENTIAL_SUFFIX = "-creds"
K8SD_SNAP_SOCKET = "/var/snap/k8s/common/var/lib/k8sd/state/control.socket"
SERVICE_LB_ANNOTATION = "io.cilium/lb-ipam-ips"


def validate_cidrs(ip_ranges: str, separator: str = ","):
    for ip_cidr in ip_ranges.split(separator):
        ipaddress.ip_network(ip_cidr)


def k8s_addons_questions():
    return {
        "loadbalancer": PromptQuestion(
            "Load balancer CIDR ranges (supports multiple cidrs, comma separated)",
            default_value="10.20.21.16/28",
            validation_function=validate_cidrs,
        ),
    }


class DeployK8SApplicationStep(DeployMachineApplicationStep):
    """Deploy K8S application using Terraform"""

    _ADDONS_CONFIG = K8S_ADDONS_CONFIG_KEY

    def __init__(
        self,
        client: Client,
        manifest: Manifest,
        jhelper: JujuHelper,
        model: str,
        deployment_preseed: dict | None = None,
        accept_defaults: bool = False,
        refresh: bool = False,
    ):
        super().__init__(
            client,
            manifest,
            jhelper,
            K8S_CONFIG_KEY,
            APPLICATION,
            model,
            "k8s-plan",
            "Deploy K8S",
            "Deploying K8S",
            refresh,
        )

        self.preseed = deployment_preseed or {}
        self.accept_defaults = accept_defaults
        self.variables = {}

    def prompt(self, console: Console | None = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        self.variables = load_answers(self.client, self._ADDONS_CONFIG)
        self.variables.setdefault("k8s-addons", {})

        k8s_addons_bank = QuestionBank(
            questions=k8s_addons_questions(),
            console=console,  # type: ignore
            preseed=self.preseed.get("k8s-addons"),
            previous_answers=self.variables.get("k8s-addons", {}),
            accept_defaults=self.accept_defaults,
        )
        self.variables["k8s-addons"][
            "loadbalancer"
        ] = k8s_addons_bank.loadbalancer.ask()

        LOG.debug(self.variables)
        write_answers(self.client, self._ADDONS_CONFIG, self.variables)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        # No need to prompt for questions in case of refresh
        if self.refresh:
            return False

        return True

    def get_application_timeout(self) -> int:
        return K8S_APP_TIMEOUT


class AddK8SUnitsStep(AddMachineUnitsStep):
    """Add K8S Unit."""

    def __init__(
        self,
        client: Client,
        names: list[str] | str,
        jhelper: JujuHelper,
        model: str,
    ):
        super().__init__(
            client,
            names,
            jhelper,
            K8S_CONFIG_KEY,
            APPLICATION,
            model,
            "Add K8S unit",
            "Adding K8S unit to machine",
        )

    def get_unit_timeout(self) -> int:
        return K8S_UNIT_TIMEOUT


class RemoveK8SUnitStep(RemoveMachineUnitStep):
    """Remove K8S Unit."""

    def __init__(
        self,
        client: Client,
        names: list[str] | str,
        jhelper: JujuHelper,
        model: str,
        application: str,
    ):
        super().__init__(
            client,
            names,
            jhelper,
            K8S_CONFIG_KEY,
            application,
            model,
            "Remove K8S unit",
            "Removing K8S unit from machine",
        )

    def get_unit_timeout(self) -> int:
        return K8S_UNIT_TIMEOUT


class AddK8SCloudStep(BaseStep, JujuStepHelper):

    _KUBECONFIG = K8S_KUBECONFIG_KEY

    def __init__(self, client: Client, jhelper: JujuHelper):
        super().__init__("Add K8S cloud", "Adding K8S cloud to Juju controller")
        self.client = client
        self.jhelper = jhelper
        self.name = K8S_CLOUD
        self.credential_name = f"{K8S_CLOUD}{CREDENTIAL_SUFFIX}"

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        clouds = run_sync(self.jhelper.get_clouds())
        LOG.debug(f"Clouds registered in the controller: {clouds}")
        # TODO(hemanth): Need to check if cloud credentials are also created?
        if f"cloud-{self.name}" in clouds.keys():
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Add k8s cloud to Juju controller."""
        try:
            kubeconfig = read_config(self.client, self._KUBECONFIG)
            run_sync(
                self.jhelper.add_k8s_cloud(self.name, self.credential_name, kubeconfig)
            )
        except (ConfigItemNotFoundException, UnsupportedKubeconfigException) as e:
            LOG.debug("Failed to add k8s cloud to Juju controller", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class StoreK8SKubeConfigStep(BaseStep, JujuStepHelper):
    _KUBECONFIG = K8S_KUBECONFIG_KEY

    def __init__(self, client: Client, jhelper: JujuHelper, model: str):
        super().__init__(
            "Store K8S kubeconfig",
            "Storing K8S configuration in sunbeam database",
        )
        self.client = client
        self.jhelper = jhelper
        self.model = model

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            read_config(self.client, self._KUBECONFIG)
        except ConfigItemNotFoundException:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Store K8S config in clusterd."""
        try:
            unit = run_sync(self.jhelper.get_leader_unit(APPLICATION, self.model))
            LOG.debug(unit)
            result = run_sync(
                self.jhelper.run_action(unit, self.model, "get-kubeconfig")
            )
            LOG.debug(result)
            if not result.get("kubeconfig"):
                return Result(
                    ResultType.FAILED,
                    "ERROR: Failed to retrieve kubeconfig",
                )
            kubeconfig = yaml.safe_load(result["kubeconfig"])
            update_config(self.client, self._KUBECONFIG, kubeconfig)
        except (
            ApplicationNotFoundException,
            LeaderNotFoundException,
            ActionFailedException,
        ) as e:
            LOG.debug("Failed to store k8s config", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
