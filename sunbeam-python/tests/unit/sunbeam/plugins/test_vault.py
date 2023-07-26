# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, Mock

import pytest
import tenacity
from requests.exceptions import ConnectionError

from sunbeam.commands.terraform import TerraformException
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import (
    ActionFailedException,
    LeaderNotFoundException,
    TimeoutException,
)
from sunbeam.plugins.vault import plugin as vault_plugin


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.plugins.vault.plugin.run_sync", run_sync)
    yield
    loop.close()


@pytest.fixture()
def cclient():
    with patch("sunbeam.plugins.interface.v1.base.Client") as p:
        yield p


@pytest.fixture()
def jhelper():
    yield AsyncMock()


@pytest.fixture()
def tfhelper():
    yield Mock(path=Path())


@pytest.fixture()
def vaultplugin():
    with patch("sunbeam.plugins.vault.plugin.VaultPlugin") as p:
        yield p


@pytest.fixture()
def hvac():
    with patch("sunbeam.plugins.vault.plugin.hvac") as p:
        yield p


@pytest.fixture()
def vault_status():
    with patch("sunbeam.plugins.vault.plugin.get_vault_status") as p:
        yield p


class TestEnableVaultStep:
    def test_run(self, cclient, jhelper, tfhelper, vaultplugin):
        step = vault_plugin.EnableVaultStep(vaultplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self, cclient, jhelper, tfhelper, vaultplugin):
        tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = vault_plugin.EnableVaultStep(vaultplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_application_ready.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self, cclient, jhelper, tfhelper, vaultplugin):
        jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = vault_plugin.EnableVaultStep(vaultplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestDisableVaultStep:
    def test_run(self, cclient, jhelper, tfhelper, vaultplugin):
        step = vault_plugin.DisableVaultStep(vaultplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_destroy_failed(self, cclient, jhelper, tfhelper, vaultplugin):
        tfhelper.destroy.side_effect = TerraformException("destroy failed...")

        step = vault_plugin.DisableVaultStep(vaultplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "destroy failed..."

    def test_run_waiting_timed_out(self, cclient, jhelper, tfhelper, vaultplugin):
        jhelper.wait_application_gone.side_effect = TimeoutException("timed out")

        step = vault_plugin.DisableVaultStep(vaultplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class VaultDeployed:
    def is_skip_vault_not_deployed(self, step):
        model = Mock(get_status=AsyncMock(return_value=Mock(applications={})))
        step.jhelper.get_model.return_value = model
        result = step.is_skip()
        assert result.result_type == ResultType.FAILED
        assert result.message and "not deployed" in result.message

    def is_skip_too_many_units(self, step):
        model = Mock(
            get_status=AsyncMock(
                return_value=Mock(
                    applications={
                        "vault": Mock(units={"vault/0": Mock(), "vault/1": Mock()})
                    }
                )
            )
        )
        step.jhelper.get_model.return_value = model
        result = step.is_skip()
        assert result.result_type == ResultType.FAILED
        assert result.message and "Invalid number" in result.message


class TestWaitVaultRouteableStep(VaultDeployed):
    def test_is_skip_vault_not_deployed(self, jhelper):
        self.is_skip_vault_not_deployed(vault_plugin.WaitVaultRouteableStep(jhelper))

    def test_is_skip_too_many_units(self, jhelper):
        self.is_skip_too_many_units(vault_plugin.WaitVaultRouteableStep(jhelper))

    def test_is_skip(self, jhelper, hvac):
        model = Mock(
            get_status=AsyncMock(
                return_value=Mock(
                    applications={"vault": Mock(units={"vault/0": Mock()})}
                )
            )
        )
        jhelper.get_model.return_value = model
        hvac.Client.return_value.sys.is_initialized.side_effect = ConnectionError(
            "No route to host"
        )
        step = vault_plugin.WaitVaultRouteableStep(jhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_vault_reachable(self, jhelper, hvac):
        model = Mock(
            get_status=AsyncMock(
                return_value=Mock(
                    applications={"vault": Mock(units={"vault/0": Mock()})}
                )
            )
        )
        jhelper.get_model.return_value = model
        step = vault_plugin.WaitVaultRouteableStep(jhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_unknown_error(self, jhelper, hvac):
        model = Mock(
            get_status=AsyncMock(
                return_value=Mock(
                    applications={"vault": Mock(units={"vault/0": Mock()})}
                )
            )
        )
        jhelper.get_model.return_value = model
        hvac.Client.return_value.sys.is_initialized.side_effect = ConnectionError(
            "Unknown Error"
        )
        step = vault_plugin.WaitVaultRouteableStep(jhelper)
        step.vault_address = "vault.example.com"
        result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_run(self, jhelper, hvac):
        step = vault_plugin.WaitVaultRouteableStep(jhelper)
        step.vault_address = "vault.example.com"
        result = step.run()
        assert result.result_type == ResultType.COMPLETED

    def test_run_retried(self, jhelper, hvac):
        hvac.Client.return_value.sys.is_initialized.side_effect = [
            ConnectionError("No route to host"),
            True,
        ]
        step = vault_plugin.WaitVaultRouteableStep(jhelper)
        step._retry_run.retry.wait = tenacity.wait_none()
        step.vault_address = "vault.example.com"
        result = step.run()
        assert hvac.Client.return_value.sys.is_initialized.call_count == 2
        assert result.result_type == ResultType.COMPLETED

    def test_run_vault_unknown_error(self, jhelper, hvac):
        hvac.Client.return_value.sys.is_initialized.side_effect = ConnectionError(
            "Unknown Error"
        )
        step = vault_plugin.WaitVaultRouteableStep(jhelper)
        step._retry_run.retry.wait = tenacity.wait_none()
        step.vault_address = "vault.example.com"
        result = step.run()
        assert hvac.Client.return_value.sys.is_initialized.call_count == 1
        assert result.result_type == ResultType.FAILED

    def test_run_vault_timeout(self, jhelper, hvac):
        hvac.Client.return_value.sys.is_initialized.side_effect = ConnectionError(
            "No route to host"
        )
        step = vault_plugin.WaitVaultRouteableStep(jhelper)
        step._retry_run.retry.wait = tenacity.wait_none()
        step.vault_address = "vault.example.com"
        result = step.run()
        assert hvac.Client.return_value.sys.is_initialized.call_count == 5
        assert result.result_type == ResultType.FAILED
        assert result.message and "Timeout" in result.message


class TestUnsealVaultStep(VaultDeployed):
    def test_is_skip_vault_not_deployed(self, vaultplugin, jhelper):
        self.is_skip_vault_not_deployed(
            vault_plugin.UnsealVaultStep(vaultplugin, jhelper)
        )

    def test_is_skip_too_many_units(self, vaultplugin, jhelper):
        self.is_skip_too_many_units(vault_plugin.UnsealVaultStep(vaultplugin, jhelper))

    def test_is_skip(self, vaultplugin, jhelper, hvac):
        model = Mock(
            get_status=AsyncMock(
                return_value=Mock(
                    applications={"vault": Mock(units={"vault/0": Mock()})}
                )
            )
        )
        jhelper.get_model.return_value = model
        step = vault_plugin.UnsealVaultStep(vaultplugin, jhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_not_initialized(self, vaultplugin, jhelper, hvac):
        model = Mock(
            get_status=AsyncMock(
                return_value=Mock(
                    applications={"vault": Mock(units={"vault/0": Mock()})}
                )
            )
        )
        jhelper.get_model.return_value = model
        hvac.Client.return_value.sys.is_initialized.return_value = False
        step = vault_plugin.UnsealVaultStep(vaultplugin, jhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_already_unsealed(self, vaultplugin, jhelper, hvac):
        model = Mock(
            get_status=AsyncMock(
                return_value=Mock(
                    applications={"vault": Mock(units={"vault/0": Mock()})}
                )
            )
        )
        jhelper.get_model.return_value = model
        hvac.Client.return_value.sys.is_sealed.return_value = False
        step = vault_plugin.UnsealVaultStep(vaultplugin, jhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, vaultplugin, jhelper, hvac):
        sys = hvac.Client.return_value.sys
        sys.is_initialized.return_value = False
        sys.is_sealed.return_value = True
        step = vault_plugin.UnsealVaultStep(vaultplugin, jhelper)
        step.vault_address = "vault.example.com"
        result = step.run()
        sys.initialize.assert_called_once()
        sys.submit_unseal_keys.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_already_initialized(self, vaultplugin, jhelper, hvac):
        sys = hvac.Client.return_value.sys
        sys.is_initialized.return_value = True
        sys.is_sealed.return_value = True
        step = vault_plugin.UnsealVaultStep(vaultplugin, jhelper)
        step.vault_address = "vault.example.com"
        result = step.run()
        sys.initialize.assert_not_called()
        sys.submit_unseal_keys.assert_called_once()
        assert result.result_type == ResultType.COMPLETED


class TestAuthoriseVaultStep(VaultDeployed):
    def test_is_skip_vault_not_deployed(self, vaultplugin, jhelper):
        self.is_skip_vault_not_deployed(
            vault_plugin.AuthoriseVaultStep(vaultplugin, jhelper)
        )

    def test_is_skip_too_many_units(self, vaultplugin, jhelper):
        self.is_skip_too_many_units(
            vault_plugin.AuthoriseVaultStep(vaultplugin, jhelper)
        )

    def test_is_skip_vault_application_active(self, vaultplugin, jhelper, vault_status):
        vault_status.return_value = Mock(status=Mock(status="active"))
        step = vault_plugin.AuthoriseVaultStep(vaultplugin, jhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_vault_unit_active(self, vaultplugin, jhelper, vault_status):
        vault_status.return_value = Mock(
            units={"vault/0": Mock(workload_status=Mock(status="active"))}
        )
        step = vault_plugin.AuthoriseVaultStep(vaultplugin, jhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_vault_not_initialized(
        self, vaultplugin, jhelper, vault_status, hvac
    ):
        vault_status.return_value = Mock(
            units={
                "vault/0": Mock(
                    workload_status=Mock(status="blocked", info="authorise-charm")
                )
            }
        )
        hvac.Client.return_value.sys.is_initialized.return_value = False
        step = vault_plugin.AuthoriseVaultStep(vaultplugin, jhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_vault_is_sealed(self, vaultplugin, jhelper, vault_status, hvac):
        vault_status.return_value = Mock(
            units={
                "vault/0": Mock(
                    workload_status=Mock(status="blocked", info="authorise-charm")
                )
            }
        )
        hvac.Client.return_value.sys.is_sealed.return_value = True
        step = vault_plugin.AuthoriseVaultStep(vaultplugin, jhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_run(self, vaultplugin, jhelper, vault_status, hvac):
        vault_status.return_value = Mock(
            units={
                "vault/0": Mock(
                    workload_status=Mock(status="blocked", info="authorise-charm")
                )
            }
        )
        step = vault_plugin.AuthoriseVaultStep(vaultplugin, jhelper)
        step.vault_address = "vault.example.com"
        result = step.run()
        assert result.result_type == ResultType.COMPLETED

    def test_run_failed_to_get_leader_unit(
        self, vaultplugin, jhelper, vault_status, hvac
    ):
        vault_status.return_value = Mock(
            units={
                "vault/0": Mock(
                    workload_status=Mock(status="blocked", info="authorise-charm")
                )
            }
        )
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "Failed to get leader unit"
        )
        step = vault_plugin.AuthoriseVaultStep(vaultplugin, jhelper)
        step.vault_address = "vault.example.com"
        result = step.run()
        assert result.result_type == ResultType.FAILED

    def test_run_authorise_failed(self, vaultplugin, jhelper, vault_status, hvac):
        vault_status.return_value = Mock(
            units={
                "vault/0": Mock(
                    workload_status=Mock(status="blocked", info="authorise-charm")
                )
            }
        )
        jhelper.run_action.side_effect = ActionFailedException("timed out")
        step = vault_plugin.AuthoriseVaultStep(vaultplugin, jhelper)
        step.vault_address = "vault.example.com"
        result = step.run()
        assert result.result_type == ResultType.FAILED
