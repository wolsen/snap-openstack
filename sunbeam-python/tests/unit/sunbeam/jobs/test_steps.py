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

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.commands.terraform import TerraformException
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import ApplicationNotFoundException, TimeoutException
from sunbeam.jobs.manifest import Manifest
from sunbeam.jobs.steps import (
    AddMachineUnitStep,
    DeployMachineApplicationStep,
    RemoveMachineUnitStep,
)


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.jobs.steps.run_sync", run_sync)
    yield
    loop.close()


@pytest.fixture()
def cclient():
    with patch("sunbeam.jobs.steps.Client") as p:
        yield p


@pytest.fixture()
def jhelper():
    yield AsyncMock()


@pytest.fixture()
def read_config():
    with patch("sunbeam.jobs.steps.read_config", return_value={}) as p:
        yield p


@pytest.fixture()
def manifest():
    with patch.object(Manifest, "load_latest_from_clusterdb_on_default") as p:
        yield p


class TestDeployMachineApplicationStep:
    def test_is_skip(self, cclient, jhelper):
        jhelper.get_application.side_effect = ApplicationNotFoundException("not found")

        step = DeployMachineApplicationStep(
            manifest, jhelper, "tfconfig", "app1", "model1", "fake-plan"
        )
        result = step.is_skip()

        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_application_already_deployed(self, cclient, jhelper):
        step = DeployMachineApplicationStep(
            manifest, jhelper, "tfconfig", "app1", "model1", "fake-plan"
        )
        result = step.is_skip()

        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run_pristine_installation(self, cclient, jhelper, manifest):
        jhelper.get_application.side_effect = ApplicationNotFoundException("not found")

        step = DeployMachineApplicationStep(
            manifest, jhelper, "tfconfig", "app1", "model1", "fake-plan"
        )
        result = step.run()

        jhelper.get_application.assert_called_once()
        manifest.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_already_deployed(self, cclient, jhelper, manifest):
        tfconfig = "tfconfig"
        tfplan = "fake-plan"
        machines = ["1", "2"]
        application = Mock(units=[Mock(machine=Mock(id=m)) for m in machines])
        jhelper.get_application.return_value = application

        step = DeployMachineApplicationStep(
            manifest, jhelper, tfconfig, "app1", "model1", tfplan
        )
        result = step.run()

        jhelper.get_application.assert_called_once()
        manifest.update_tfvars_and_apply_tf.assert_called_with(
            tfplan=tfplan,
            tfvar_config=tfconfig,
            override_tfvars={"machine_ids": machines},
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self, cclient, jhelper, manifest):
        manifest.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = DeployMachineApplicationStep(
            manifest, jhelper, "tfconfig", "app1", "model1", "fake-plan"
        )
        result = step.run()

        manifest.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self, cclient, jhelper, manifest):
        jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = DeployMachineApplicationStep(
            manifest, jhelper, "tfconfig", "app1", "model1", "fake-plan"
        )
        result = step.run()

        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestAddMachineUnitStep:
    def test_is_skip(self, cclient, jhelper):
        step = AddMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self, cclient, jhelper):
        cclient().cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "Node missing..."
        )

        step = AddMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.is_skip()

        cclient().cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Node missing..."

    def test_is_skip_application_missing(self, cclient, jhelper):
        jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = AddMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.is_skip()

        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application app1 has not been deployed"

    def test_is_skip_unit_already_deployed(self, cclient, jhelper):
        id = "1"
        cclient().cluster.get_node_info.return_value = {"machineid": id}
        jhelper.get_application.return_value = Mock(units=[Mock(machine=Mock(id=id))])

        step = AddMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.is_skip()

        cclient().cluster.get_node_info.assert_called_once()
        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, cclient, jhelper, read_config):
        step = AddMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self, cclient, jhelper, read_config):
        jhelper.add_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = AddMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.run()

        jhelper.add_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_timeout(self, cclient, jhelper, read_config):
        jhelper.wait_unit_ready.side_effect = TimeoutException("timed out")

        step = AddMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.run()

        jhelper.wait_unit_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveMachineUnitStep:
    def test_is_skip(self, cclient, jhelper):
        id = "1"
        cclient().cluster.get_node_info.return_value = {"machineid": id}
        jhelper.get_application.return_value = Mock(units=[Mock(machine=Mock(id=id))])

        step = RemoveMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.is_skip()

        cclient().cluster.get_node_info.assert_called_once()
        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self, cclient, jhelper):
        cclient().cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "Node missing..."
        )

        step = RemoveMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.is_skip()

        cclient().cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_missing(self, cclient, jhelper):
        jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.is_skip()

        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_unit_missing(self, cclient, jhelper):
        cclient().cluster.get_node_info.return_value = {}
        jhelper.get_application.return_value = Mock(units=[])

        step = RemoveMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.is_skip()

        cclient().cluster.get_node_info.assert_called_once()
        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, cclient, jhelper, read_config):
        step = RemoveMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self, cclient, jhelper, read_config):
        jhelper.remove_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.run()

        jhelper.remove_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_timeout(self, cclient, jhelper, read_config):
        jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = RemoveMachineUnitStep("app1", jhelper, "tfconfig", "app1", "model1")
        result = step.run()

        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
