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
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sunbeam.commands.terraform import TerraformException
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import TimeoutException
from sunbeam.plugins.monitoring import plugin as monitoring_plugin


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.plugins.monitoring.plugin.run_sync", run_sync)
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
def monitoringplugin():
    with patch("sunbeam.plugins.monitoring.plugin.MonitoringPlugin") as p:
        yield p


class TestDeployCosStep:
    def test_run(self, cclient, jhelper, tfhelper, monitoringplugin):
        step = monitoring_plugin.DeployCosStep(monitoringplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self, cclient, jhelper, tfhelper, monitoringplugin):
        tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = monitoring_plugin.DeployCosStep(monitoringplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_active.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self, cclient, jhelper, tfhelper, monitoringplugin):
        jhelper.wait_until_active.side_effect = TimeoutException("timed out")

        step = monitoring_plugin.DeployCosStep(monitoringplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveCosStep:
    def test_run(self, cclient, jhelper, tfhelper, monitoringplugin):
        step = monitoring_plugin.RemoveCosStep(monitoringplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.destroy.assert_called_once()
        jhelper.wait_model_gone.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_destroy_failed(self, cclient, jhelper, tfhelper, monitoringplugin):
        tfhelper.destroy.side_effect = TerraformException("destroy failed...")

        step = monitoring_plugin.RemoveCosStep(monitoringplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.destroy.assert_called_once()
        jhelper.wait_model_gone.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "destroy failed..."

    def test_run_waiting_timed_out(self, cclient, jhelper, tfhelper, monitoringplugin):
        jhelper.wait_model_gone.side_effect = TimeoutException("timed out")

        step = monitoring_plugin.RemoveCosStep(monitoringplugin, tfhelper, jhelper)
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.destroy.assert_called_once()
        jhelper.wait_model_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestDeployGrafanaAgentStep:
    def test_run(self, cclient, jhelper, tfhelper, monitoringplugin):
        step = monitoring_plugin.DeployGrafanaAgentStep(
            monitoringplugin, tfhelper, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self, cclient, jhelper, tfhelper, monitoringplugin):
        tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = monitoring_plugin.DeployGrafanaAgentStep(
            monitoringplugin, tfhelper, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_application_ready.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self, cclient, jhelper, tfhelper, monitoringplugin):
        jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = monitoring_plugin.DeployGrafanaAgentStep(
            monitoringplugin, tfhelper, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveGrafanaAgentStep:
    def test_run(self, cclient, jhelper, tfhelper, monitoringplugin):
        step = monitoring_plugin.RemoveGrafanaAgentStep(
            monitoringplugin, tfhelper, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_destroy_failed(self, cclient, jhelper, tfhelper, monitoringplugin):
        tfhelper.destroy.side_effect = TerraformException("destroy failed...")

        step = monitoring_plugin.RemoveGrafanaAgentStep(
            monitoringplugin, tfhelper, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "destroy failed..."

    def test_run_waiting_timed_out(self, cclient, jhelper, tfhelper, monitoringplugin):
        jhelper.wait_application_gone.side_effect = TimeoutException("timed out")

        step = monitoring_plugin.RemoveGrafanaAgentStep(
            monitoringplugin, tfhelper, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
