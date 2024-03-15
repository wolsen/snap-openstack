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
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    NodeNotExistInClusterException,
)
from sunbeam.commands.terraform import TerraformException
from sunbeam.jobs.common import BaseStep, Result, ResultType, read_config, update_config
from sunbeam.jobs.deployment import PROXY_CONFIG_KEY, Deployment
from sunbeam.jobs.juju import (
    ApplicationNotFoundException,
    JujuHelper,
    TimeoutException,
    run_sync,
)
from sunbeam.jobs.manifest import Manifest
from sunbeam.jobs.questions import (
    ConfirmQuestion,
    PromptQuestion,
    QuestionBank,
    load_answers,
    write_answers,
)

LOG = logging.getLogger(__name__)


class DeployMachineApplicationStep(BaseStep):
    """Base class to deploy machine application using Terraform cloud"""

    def __init__(
        self,
        client: Client,
        manifest: Manifest,
        jhelper: JujuHelper,
        config: str,
        application: str,
        model: str,
        tfplan: str,
        banner: str = "",
        description: str = "",
        refresh: bool = False,
    ):
        super().__init__(banner, description)
        self.manifest = manifest
        self.jhelper = jhelper
        self.config = config
        self.application = application
        self.model = model
        self.client = client
        self.tfplan = tfplan
        # Set refresh flag to True to redeploy the application
        self.refresh = refresh

    def extra_tfvars(self) -> dict:
        return {}

    def get_application_timeout(self) -> int:
        return 600

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if self.refresh:
            return Result(ResultType.COMPLETED)

        try:
            run_sync(self.jhelper.get_application(self.application, self.model))
        except ApplicationNotFoundException:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to deploy sunbeam machine"""
        machine_ids = []
        try:
            app = run_sync(self.jhelper.get_application(self.application, self.model))
            machine_ids.extend(unit.machine.id for unit in app.units)
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))

        try:
            extra_tfvars = self.extra_tfvars()
            extra_tfvars.update(
                {
                    "machine_ids": machine_ids,
                    "machine_model": self.model,
                }
            )
            self.manifest.update_tfvars_and_apply_tf(
                self.client,
                tfplan=self.tfplan,
                tfvar_config=self.config,
                override_tfvars=extra_tfvars,
            )
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        # Note(gboutry): application is in state unknown when it's deployed
        # without units
        try:
            run_sync(
                self.jhelper.wait_application_ready(
                    self.application,
                    self.model,
                    accepted_status=["active", "unknown"],
                    timeout=self.get_application_timeout(),
                )
            )
        except TimeoutException as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddMachineUnitsStep(BaseStep):
    """Base class to add units of machine application"""

    def __init__(
        self,
        client: Client,
        names: list[str] | str,
        jhelper: JujuHelper,
        config: str,
        application: str,
        model: str,
        banner: str = "",
        description: str = "",
    ):
        super().__init__(banner, description)
        self.client = client
        if isinstance(names, str):
            names = [names]
        self.names = names
        self.jhelper = jhelper
        self.config = config
        self.application = application
        self.model = model
        self.to_deploy = set()

    def get_unit_timeout(self) -> int:
        return 600  # 10 minutes

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if len(self.names) == 0:
            return Result(ResultType.SKIPPED)
        nodes: list[dict] = self.client.cluster.list_nodes()

        filtered_nodes = list(filter(lambda node: node["name"] in self.names, nodes))
        if len(filtered_nodes) != len(self.names):
            filtered_node_names = [node["name"] for node in filtered_nodes]
            missing_nodes = set(self.names) - set(filtered_node_names)
            return Result(
                ResultType.FAILED,
                f"Nodes '{','.join(missing_nodes)}' do not exist in cluster database",
            )

        nodes_without_machine_id = []

        for node in filtered_nodes:
            node_machine_id = node.get("machineid", -1)
            if node_machine_id == -1:
                nodes_without_machine_id.append(node["name"])
                continue
            self.to_deploy.add(str(node_machine_id))

        if len(nodes_without_machine_id) > 0:
            return Result(
                ResultType.FAILED,
                f"Nodes '{','.join(nodes_without_machine_id)}' do not have machine id,"
                " are they deployed?",
            )
        try:
            app = run_sync(self.jhelper.get_application(self.application, self.model))
        except ApplicationNotFoundException:
            return Result(
                ResultType.FAILED,
                f"Application {self.application} has not been deployed",
            )

        deployed_units_machine_ids = set(unit.machine.id for unit in app.units)
        self.to_deploy -= deployed_units_machine_ids
        if len(self.to_deploy) == 0:
            return Result(ResultType.SKIPPED, "No new units to deploy")

        return Result(ResultType.COMPLETED)

    def add_machine_id_to_tfvar(self) -> None:
        """Add machine id to terraform vars saved in cluster db."""
        try:
            tfvars = read_config(self.client, self.config)
        except ConfigItemNotFoundException:
            tfvars = {}

        machine_ids = set(tfvars.get("machine_ids", []))

        if len(self.to_deploy) > 0 and self.to_deploy.issubset(machine_ids):
            LOG.debug("All machine ids are already in tfvars, skipping update")
            return

        machine_ids.update(self.to_deploy)
        tfvars.update({"machine_ids": sorted(machine_ids)})
        update_config(self.client, self.config, tfvars)

    def run(self, status: Optional[Status] = None) -> Result:
        """Add unit to machine application on Juju model."""
        try:
            units = run_sync(
                self.jhelper.add_unit(
                    self.application, self.model, sorted(self.to_deploy)
                )
            )
            self.add_machine_id_to_tfvar()
            run_sync(
                self.jhelper.wait_units_ready(
                    units,
                    self.model,
                    timeout=self.get_unit_timeout(),
                )
            )
        except (ApplicationNotFoundException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveMachineUnitStep(BaseStep):
    """Base class to remove unit of machine application"""

    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        config: str,
        application: str,
        model: str,
        banner: str = "",
        description: str = "",
    ):
        super().__init__(banner, description)
        self.client = client
        self.name = name
        self.jhelper = jhelper
        self.config = config
        self.application = application
        self.model = model
        self.machine_id = ""
        self.unit = None

    def get_unit_timeout(self) -> int:
        return 600  # 10 minutes

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node = self.client.cluster.get_node_info(self.name)
            self.machine_id = str(node.get("machineid"))
        except NodeNotExistInClusterException:
            LOG.debug(f"Machine {self.name} does not exist, skipping.")
            return Result(ResultType.SKIPPED)

        try:
            app = run_sync(self.jhelper.get_application(self.application, self.model))
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))
            return Result(
                ResultType.SKIPPED,
                "Application {self.application} has not been deployed yet",
            )

        for unit in app.units:
            if unit.machine.id == self.machine_id:
                LOG.debug(f"Unit {unit.name} is deployed on machine: {self.machine_id}")
                self.unit = unit.name
                return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Remove unit from machine application on Juju model."""
        try:
            run_sync(
                self.jhelper.remove_unit(self.application, str(self.unit), self.model)
            )
            run_sync(
                self.jhelper.wait_application_ready(
                    self.application,
                    self.model,
                    accepted_status=["active", "unknown"],
                    timeout=self.get_unit_timeout(),
                )
            )
        except (ApplicationNotFoundException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


def proxy_questions():
    return {
        "proxy_required": ConfirmQuestion(
            "Configure proxy for access to external network resources?",
            default_value=False,
        ),
        "http_proxy": PromptQuestion(
            "Enter value for http_proxy:",
        ),
        "https_proxy": PromptQuestion(
            "Enter value for https_proxy:",
        ),
        "no_proxy": PromptQuestion(
            "Enter value for no_proxy:",
        ),
    }


class PromptForProxyStep(BaseStep):
    def __init__(
        self,
        deployment: Deployment,
        deployment_preseed: dict | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__("Proxy Settings", "Query user for proxy settings")
        self.deployment = deployment
        self.preseed = deployment_preseed or {}
        self.accept_defaults = accept_defaults
        self.client = deployment.get_client()

    def prompt(self, console: Optional[Console] = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        self.variables = load_answers(self.client, PROXY_CONFIG_KEY)
        self.variables.setdefault("proxy", {})

        previous_answers = self.variables.get("proxy", {})
        LOG.debug(f"Previos anders: {previous_answers}")
        if not (
            previous_answers.get("http_proxy")
            and previous_answers.get("https_proxy")  # noqa: W503
            and previous_answers.get("no_proxy")  # noqa: W503
        ):
            # Fill with defaults coming from deployment default_proxy_settings
            default_proxy_settings = self.deployment.get_default_proxy_settings()
            default_proxy_settings = {
                k.lower(): v for k, v in default_proxy_settings.items() if v
            }

            # If proxies are coming from defaults, change the default for
            # proxy_required to True. For example in local provider deployment,
            # default for proxy_required will be "y" if proxies exists in
            # /etc/environment
            if default_proxy_settings:
                previous_answers["proxy_required"] = True

            previous_answers.update(default_proxy_settings)

        proxy_bank = QuestionBank(
            questions=proxy_questions(),
            console=console,
            preseed=self.preseed.get("proxy"),
            previous_answers=previous_answers,
            accept_defaults=self.accept_defaults,
        )

        self.variables["proxy"]["proxy_required"] = proxy_bank.proxy_required.ask()
        if self.variables["proxy"]["proxy_required"]:
            self.variables["proxy"]["http_proxy"] = proxy_bank.http_proxy.ask()
            self.variables["proxy"]["https_proxy"] = proxy_bank.https_proxy.ask()
            self.variables["proxy"]["no_proxy"] = proxy_bank.no_proxy.ask()

        write_answers(self.client, PROXY_CONFIG_KEY, self.variables)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return True

    def run(self, status: Optional[Status]) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        return Result(ResultType.COMPLETED)
