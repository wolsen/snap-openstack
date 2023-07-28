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

"""Vault plugin.

Vault secure, store and tightly control access to tokens, passwords, certificates,
encryption keys for protecting secrets and other sensitive data.

Library requirements:
- hvac
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

import click
import hvac
import tenacity
from juju.client.client import ApplicationStatus, FullStatus
from packaging.version import Version
from requests.exceptions import ConnectionError
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.microk8s import MICROK8S_CLOUD
from sunbeam.commands.openstack import OPENSTACK_MODEL, PatchLoadBalancerServicesStep
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.jobs.common import BaseStep, Result, ResultType, run_plan
from sunbeam.jobs.juju import (
    ActionFailedException,
    JujuHelper,
    JujuWaitException,
    LeaderNotFoundException,
    TimeoutException,
    run_sync,
)
from sunbeam.plugins.interface.v1.base import EnableDisablePlugin

LOG = logging.getLogger(__name__)
console = Console()

APPLICATION = "vault"
VAULT_DEPLOY_TIMEOUT = 1200  # 20 minutes


class VaultPluginException(Exception):
    pass


class EnableVaultStep(BaseStep, JujuStepHelper):
    """Deploy Vault using Terraform cloud"""

    def __init__(
        self,
        plugin: "VaultPlugin",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Deploying Vault", "Deploying Vault")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL
        self.cloud = MICROK8S_CLOUD
        self.read_config = lambda: plugin.get_plugin_info().get("config", {})
        self.update_config = lambda c: plugin.update_plugin_info({"config": c})

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            run_sync(self.jhelper.get_model(self.model))
        except ModuleNotFoundError:
            return Result(ResultType.FAILED, "Model not found")
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""

        config = self.read_config()
        tfvars = {"model": self.model, "channel": "latest/edge"}
        config.update(tfvars)
        self.update_config(config)
        self.tfhelper.write_tfvars(tfvars)
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error deploying vault")
            return Result(ResultType.FAILED, str(e))

        try:
            run_sync(
                self.jhelper.wait_application_ready(
                    APPLICATION,
                    self.model,
                    # vault waits for unsealing in blocked status
                    accepted_status=["active", "blocked"],
                    timeout=VAULT_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug("Failed to deploy vault", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DisableVaultStep(BaseStep, JujuStepHelper):
    """Remove Vault using Terraform cloud"""

    def __init__(
        self,
        plugin: "VaultPlugin",
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__("Removing Vault", "Removing Vault")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL
        self.cloud = MICROK8S_CLOUD
        self.read_config = lambda: plugin.get_plugin_info().get("config", {})
        self.update_config = lambda c: plugin.update_plugin_info({"config": c})

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""

        config = self.read_config()
        tfvars = {"model": self.model, "channel": "latest/edge"}
        config.update(tfvars)
        self.update_config(config)
        self.tfhelper.write_tfvars(tfvars)
        try:
            self.tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying vault")
            return Result(ResultType.FAILED, str(e))

        try:
            run_sync(
                self.jhelper.wait_application_gone(
                    [APPLICATION],
                    self.model,
                    timeout=VAULT_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug("Failed to destroy vault", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


def get_vault_status(jhelper: JujuHelper, model: str) -> ApplicationStatus:
    model_impl = run_sync(jhelper.get_model(model))
    model_status: FullStatus = run_sync(model_impl.get_status(["vault"]))
    vault_status = model_status.applications.get("vault")

    if vault_status is None:
        raise VaultPluginException("Vault not deployed")

    if len(vault_status.units) != 1:
        raise VaultPluginException("Invalid number of Vault units deployed")

    return vault_status


class WaitVaultRouteableStep(BaseStep, JujuStepHelper):
    """Retry getting route to Vault"""

    def __init__(self, jhelper: JujuHelper):
        super().__init__("Waiting for Vault", "Waiting for Vault to be routeable")
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            vault_status = get_vault_status(self.jhelper, self.model)
        except VaultPluginException as e:
            return Result(ResultType.FAILED, str(e))

        vault_ip = vault_status.public_address
        self.vault_address = f"http://{vault_ip}:8200"
        vault = hvac.Client(url=self.vault_address)
        try:
            vault.sys.is_initialized()
        except ConnectionError as e:
            if "No route to host" in str(e):
                return Result(ResultType.COMPLETED)
            LOG.debug("Failed to reach Vault", exc_info=True)
            return Result(ResultType.FAILED, "Failed to reach Vault")

        return Result(ResultType.SKIPPED)

    @tenacity.retry(
        wait=tenacity.wait_fixed(5),
        stop=tenacity.stop_after_delay(30) | tenacity.stop_after_attempt(5),
        retry=tenacity.retry_if_exception_type(ConnectionError)
        & tenacity.retry_if_exception_message(match=r".*No route to host"),  # noqa
    )
    def _retry_run(self, vault: hvac.Client):
        vault.sys.is_initialized()

    def run(self, status: Optional[Status] = None) -> Result:
        """Block until Vault is routeable"""
        vault = hvac.Client(url=self.vault_address)

        try:
            self._retry_run(vault)
        except tenacity.RetryError:
            return Result(
                ResultType.FAILED,
                "Timeout while waiting for Vault to be reachable",
            )
        except ConnectionError:
            LOG.debug("Failed to reach Vault", exc_info=True)
            return Result(ResultType.FAILED, "Failed to reach Vault")

        return Result(ResultType.COMPLETED)


class UnsealVaultStep(BaseStep, JujuStepHelper):
    """Unseal Vault using hvac library"""

    def __init__(self, plugin: "VaultPlugin", jhelper: JujuHelper):
        super().__init__("Unsealing Vault", "Unsealing Vault automatically")
        self.read_keys = lambda: plugin.get_plugin_info().get("keys", [])
        self.update_keys = lambda k: plugin.update_plugin_info({"keys": k})
        self.read_token = lambda: plugin.get_plugin_info().get("root_token")
        self.update_token = lambda t: plugin.update_plugin_info({"root_token": t})
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            vault_status = get_vault_status(self.jhelper, self.model)
        except VaultPluginException as e:
            return Result(ResultType.FAILED, str(e))

        vault_ip = vault_status.public_address

        self.vault_address = f"http://{vault_ip}:8200"
        vault = hvac.Client(url=self.vault_address)

        if vault.sys.is_initialized() and not vault.sys.is_sealed():
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Initialize and unseal vault"""
        vault = hvac.Client(url=self.vault_address)
        if not vault.sys.is_initialized():
            initialize_result = vault.sys.initialize(
                secret_shares=1, secret_threshold=1
            )
            keys = initialize_result["keys"]
            self.update_keys(keys)
            self.update_token(initialize_result["root_token"])
        else:
            keys = self.read_keys()
            vault.token = self.read_token()

        if vault.sys.is_sealed():
            vault.sys.submit_unseal_keys(keys)

        return Result(ResultType.COMPLETED)


class AuthoriseVaultStep(BaseStep, JujuStepHelper):
    """Authorise Vault charm to access Vault"""

    def __init__(self, plugin: "VaultPlugin", jhelper: JujuHelper):
        super().__init__("Authorising Vault", "Authorising Vault charm to access Vault")
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL
        self.read_token = lambda: plugin.get_plugin_info().get("root_token")

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            vault_status = get_vault_status(self.jhelper, self.model)
        except VaultPluginException as e:
            return Result(ResultType.FAILED, str(e))

        if vault_status.status and vault_status.status.status == "active":
            return Result(ResultType.SKIPPED)

        for unit in vault_status.units.values():
            if unit is None or unit.workload_status is None:
                continue
            workload_status = unit.workload_status
            if workload_status.status != "blocked" or "authorise-charm" not in str(
                workload_status.info
            ):
                LOG.debug(
                    f"Vault status: {workload_status.status} - {workload_status.info}"
                )
                return Result(
                    ResultType.SKIPPED, "Vault is not in an authorisable state"
                )

        token = self.read_token()
        if token is None:
            return Result(ResultType.FAILED, "Vault token not found")
        vault_ip = vault_status.public_address
        self.vault_address = f"http://{vault_ip}:8200"
        vault = hvac.Client(url=self.vault_address)
        vault.token = token

        if not vault.sys.is_initialized():
            return Result(ResultType.FAILED, "Vault not initialized")
        if vault.sys.is_sealed():
            return Result(ResultType.FAILED, "Vault is sealed")

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Authorise Vault charm to access Vault"""
        vault = hvac.Client(url=self.vault_address)
        vault.token = self.read_token()
        token_response = vault.auth.token.create(ttl="10m")
        token = token_response["auth"]["client_token"]
        try:
            leader = run_sync(self.jhelper.get_leader_unit(APPLICATION, self.model))
        except LeaderNotFoundException:
            LOG.debug("Failed to get leader unit", exc_info=True)
            return Result(ResultType.FAILED, "Failed to get leader unit")
        try:
            run_sync(
                self.jhelper.run_action(
                    leader, self.model, "authorise-charm", {"token": token}
                )
            )
        except ActionFailedException:
            LOG.debug("Failed to authorise Vault charm", exc_info=True)
            return Result(ResultType.FAILED, "Failed to authorise Vault charm")
        return Result(ResultType.COMPLETED)


class PatchVaultLoadBalancerStep(PatchLoadBalancerServicesStep):
    SERVICES = [APPLICATION]


class VaultPlugin(EnableDisablePlugin):
    version = Version("0.0.1")

    def __init__(self) -> None:
        super().__init__(name=APPLICATION)
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
            plan="vault-plan",
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(data_location)
        plan = [
            TerraformInitStep(tfhelper),
            EnableVaultStep(self, tfhelper, jhelper),
            WaitVaultRouteableStep(jhelper),
            PatchVaultLoadBalancerStep(),
            UnsealVaultStep(self, jhelper),
            AuthoriseVaultStep(self, jhelper),
        ]

        run_plan(plan, console)

        click.echo("Vault enabled.")

    def pre_disable(self):
        self.pre_enable()

    def run_disable_plans(self):
        data_location = self.snap.paths.user_data
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / self.tfplan,
            plan="vault-plan",
            backend="http",
            data_location=data_location,
        )
        jhelper = JujuHelper(data_location)
        plan = [
            TerraformInitStep(tfhelper),
            DisableVaultStep(self, tfhelper, jhelper),
        ]

        run_plan(plan, console)
        click.echo("Vault disabled.")

    @click.command()
    def enable_plugin(self) -> None:
        """Enable Vault.

        Vault secure, store and tightly control access to tokens, passwords,
        certificates, encryption keys for protecting secrets and other sensitive data.
        """
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        super().disable_plugin()

    @click.group()
    @click.pass_context
    def vault_group(ctx, self):
        """Manage Vault."""
        ctx.obj = self

    @vault_group.command()
    @click.pass_obj
    def unseal(self) -> None:
        """Unseal Vault automatically."""
        data_location = self.snap.paths.user_data
        jhelper = JujuHelper(data_location)
        plan = [
            UnsealVaultStep(self, jhelper),
            AuthoriseVaultStep(self, jhelper),
        ]

        run_plan(plan, console)

        click.echo("Vault unsealed.")

    def commands(self) -> dict:
        """Dict of clickgroup along with commands."""
        commands = super().commands()
        try:
            enabled = self.enabled
        except ClusterServiceUnavailableException:
            LOG.debug(
                "Failed to query for plugin status, is cloud bootstrapped ?",
                exc_info=True,
            )
            enabled = False

        if enabled:
            commands.update({"init": [{"name": "vault", "command": self.vault_group}]})
        return commands
