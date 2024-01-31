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

from builtins import ConnectionRefusedError
from ssl import SSLError
from unittest.mock import Mock

import pytest
from maas.client.bones import CallError

from sunbeam.commands.deployment import DeploymentsConfig
from sunbeam.commands.maas import (
    AddMaasDeployment,
    DeploymentRolesCheck,
    MaasDeployment,
    MaasScaleJujuStep,
    MachineNetworkCheck,
    MachineRequirementsCheck,
    MachineRolesCheck,
    MachineStorageCheck,
    Networks,
    RoleTags,
    StorageTags,
    ZoneBalanceCheck,
    ZonesCheck,
)
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import ControllerNotFoundException


class TestAddMaasDeployment:
    @pytest.fixture
    def add_maas_deployment(self):
        return AddMaasDeployment(
            deployment="test_deployment",
            token="test_token",
            url="test_url",
            resource_pool="test_resource_pool",
            deployments_config=Mock(),
        )

    def test_is_skip_with_existing_deployment(self, add_maas_deployment):
        deployments_config = DeploymentsConfig(
            active="test_deployment",
            deployments=[
                MaasDeployment(
                    name="test_deployment",
                    url="test_url2",
                    resource_pool="test_resource_pool3",
                    token="test_token",
                )
            ],
        )
        add_maas_deployment.deployments_config = deployments_config
        result = add_maas_deployment.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_with_existing_url_and_resource_pool(self, add_maas_deployment):
        deployments_config = DeploymentsConfig(
            active="test_deployment",
            deployments=[
                MaasDeployment(
                    name="different_deployment",
                    url="test_url",
                    resource_pool="test_resource_pool",
                    token="test_token",
                )
            ],
        )
        add_maas_deployment.deployments_config = deployments_config
        result = add_maas_deployment.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_with_no_existing_deployment(self, add_maas_deployment):
        deployments_config = DeploymentsConfig()
        add_maas_deployment.deployments_config = deployments_config
        result = add_maas_deployment.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_successful_connection(self, add_maas_deployment, mocker):
        mocker.patch("sunbeam.commands.maas.MaasClient", autospec=True)
        result = add_maas_deployment.run()
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_connection_refused_error(self, add_maas_deployment, mocker):
        mocker.patch(
            "sunbeam.commands.maas.MaasClient",
            side_effect=ConnectionRefusedError("Connection refused"),
        )
        result = add_maas_deployment.run()
        assert result.result_type == ResultType.FAILED

    def test_run_with_ssl_error(self, add_maas_deployment, mocker):
        mocker.patch(
            "sunbeam.commands.maas.MaasClient", side_effect=SSLError("SSL error")
        )
        result = add_maas_deployment.run()
        assert result.result_type == ResultType.FAILED

    def test_run_with_call_error(self, add_maas_deployment, mocker):
        mocker.patch(
            "sunbeam.commands.maas.MaasClient",
            side_effect=CallError(
                request=dict(method="GET", uri="http://localhost:5240/MAAS"),
                response=Mock(status=401, reason="unauthorized"),
                content=b"",
                call=None,
            ),
        )
        result = add_maas_deployment.run()
        assert result.result_type == ResultType.FAILED

    def test_run_with_unknown_error(self, add_maas_deployment, mocker):
        mocker.patch(
            "sunbeam.commands.maas.MaasClient",
            side_effect=Exception("Unknown error"),
        )
        result = add_maas_deployment.run()
        assert result.result_type == ResultType.FAILED


class TestMachineRolesCheck:
    def test_run_with_no_assigned_roles(self):
        machine = {"hostname": "test_machine", "roles": []}
        check = MachineRolesCheck(machine)
        result = check.run()
        assert result.passed is False
        assert result.details["machine"] == "test_machine"

    def test_run_with_assigned_roles(self):
        machine = {"hostname": "test_machine", "roles": ["role1", "role2"]}
        check = MachineRolesCheck(machine)
        result = check.run()
        assert result.passed is True
        assert result.details["machine"] == "test_machine"


class TestMachineNetworkCheck:
    def test_run_with_incomplete_network_mapping(self, mocker):
        snap = Mock()
        mocker.patch("sunbeam.commands.maas.get_network_mapping", return_value={})
        machine = {
            "hostname": "test_machine",
            "roles": ["role1", "role2"],
            "spaces": [],
        }
        check = MachineNetworkCheck(snap, machine)
        result = check.run()
        assert result.passed is False
        assert result.details["machine"] == "test_machine"
        assert result.message and "network mapping" in result.message

    def test_run_with_no_assigned_roles(self, mocker):
        snap = Mock()
        mocker.patch(
            "sunbeam.commands.maas.get_network_mapping",
            return_value={network: "alpha" for network in Networks.values()},
        )
        machine = {"hostname": "test_machine", "roles": [], "spaces": []}
        check = MachineNetworkCheck(snap, machine)
        result = check.run()
        assert result.passed is False
        assert result.details["machine"] == "test_machine"
        assert result.message and "no role assigned" in result.message

    def test_run_with_missing_spaces(self, mocker):
        snap = Mock()
        mocker.patch(
            "sunbeam.commands.maas.get_network_mapping",
            return_value={
                **{network.value: "alpha" for network in Networks},
                **{Networks.PUBLIC.value: "beta"},
            },
        )
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.CONTROL.value],
            "spaces": ["alpha"],
        }
        check = MachineNetworkCheck(snap, machine)
        result = check.run()
        assert result.passed is False
        assert result.details["machine"] == "test_machine"
        assert result.message and "missing beta" in result.message

    def test_run_with_successful_check(self, mocker):
        snap = Mock()
        mocker.patch(
            "sunbeam.commands.maas.get_network_mapping",
            return_value={network.value: "alpha" for network in Networks},
        )
        machine = {
            "hostname": "test_machine",
            "roles": RoleTags.values(),
            "spaces": ["alpha"],
        }
        check = MachineNetworkCheck(snap, machine)
        result = check.run()
        assert result.passed is True
        assert result.details["machine"] == "test_machine"


class TestMachineStorageCheck:
    def test_run_with_no_assigned_roles(self):
        machine = {"hostname": "test_machine", "roles": [], "storage": {}}
        check = MachineStorageCheck(machine)
        result = check.run()
        assert result.passed is False
        assert result.details["machine"] == "test_machine"
        assert result.message and "machine has no role assigned" in result.message

    def test_run_with_not_storage_node(self):
        machine = {
            "hostname": "test_machine",
            "roles": ["role1", "role2"],
            "storage": {},
        }
        check = MachineStorageCheck(machine)
        result = check.run()
        assert result.passed is True
        assert result.details["machine"] == "test_machine"
        assert result.message == "not a storage node."

    def test_run_with_no_ceph_storage(self):
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.STORAGE.value],
            "storage": {},
        }
        check = MachineStorageCheck(machine)
        result = check.run()
        assert result.passed is False
        assert result.details["machine"] == "test_machine"
        assert result.message and "storage node has no ceph storage" in result.message
        assert result.diagnostics
        assert "https://maas.io/docs/using-storage-tags" in result.diagnostics

    def test_run_with_ceph_storage(self):
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.STORAGE.value],
            "storage": {StorageTags.CEPH.value: 1},
        }
        check = MachineStorageCheck(machine)
        result = check.run()
        assert result.passed is True
        assert result.details["machine"] == "test_machine"
        assert result.message and StorageTags.CEPH.value in result.message


class TestMachineRequirementsCheck:
    def test_run_with_insufficient_memory(self):
        machine = {
            "hostname": "test_machine",
            "cores": 16,
            "memory": 16384,  # 16GiB
            "roles": [RoleTags.CONTROL.value],
        }
        check = MachineRequirementsCheck(machine)
        result = check.run()
        assert result.passed is False
        assert result.details["machine"] == "test_machine"
        assert result.message and "machine does not meet requirements" in result.message

    def test_run_with_insufficient_cores(self):
        machine = {
            "hostname": "test_machine",
            "cores": 8,
            "memory": 32768,  # 32GiB
            "roles": [RoleTags.CONTROL.value],
        }
        check = MachineRequirementsCheck(machine)
        result = check.run()
        assert result.passed is False
        assert result.details["machine"] == "test_machine"
        assert result.message and "machine does not meet requirements" in result.message

    def test_run_with_sufficient_resources(self):
        machine = {
            "hostname": "test_machine",
            "cores": 16,
            "memory": 32768,  # 32GB
            "roles": [RoleTags.CONTROL.value],
        }
        check = MachineRequirementsCheck(machine)
        result = check.run()
        assert result.passed is True
        assert result.details["machine"] == "test_machine"


class TestDeploymentRolesCheck:
    def test_run_with_insufficient_roles(self):
        machines = [
            {"hostname": "machine1", "roles": ["role1", "role2"]},
            {"hostname": "machine2", "roles": ["role1"]},
            {"hostname": "machine3", "roles": ["role2"]},
        ]
        check = DeploymentRolesCheck(machines, "Role", "role1", min_count=3)
        result = check.run()
        assert result.passed is False
        assert result.message and "less than 3 Role" in result.message

    def test_run_with_sufficient_roles(self):
        machines = [
            {"hostname": "machine1", "roles": ["role1", "role2"]},
            {"hostname": "machine2", "roles": ["role1"]},
            {"hostname": "machine3", "roles": ["role1"]},
        ]
        check = DeploymentRolesCheck(machines, "Role", "role1", min_count=3)
        result = check.run()
        assert result.passed is True
        assert result.message == "Role: 3"


class TestZonesCheck:
    def test_run_with_one_zone(self):
        zones = ["zone1"]
        check = ZonesCheck(zones)
        result = check.run()
        assert result.passed is True
        assert result.message == "1 zone(s)"

    def test_run_with_two_zones(self):
        zones = ["zone1", "zone2"]
        check = ZonesCheck(zones)
        result = check.run()
        assert result.passed is False
        assert result.message == "deployment has 2 zones"

    def test_run_with_three_zones(self):
        zones = ["zone1", "zone2", "zone3"]
        check = ZonesCheck(zones)
        result = check.run()
        assert result.passed is True
        assert result.message == "3 zone(s)"


class TestZoneBalanceCheck:
    def test_run_with_balanced_roles(self):
        machines = {
            "zone1": [
                {"roles": [RoleTags.CONTROL.value, RoleTags.STORAGE.value]},
                {"roles": [RoleTags.CONTROL.value, RoleTags.COMPUTE.value]},
            ],
            "zone2": [
                {"roles": [RoleTags.CONTROL.value, RoleTags.STORAGE.value]},
                {"roles": [RoleTags.CONTROL.value, RoleTags.COMPUTE.value]},
            ],
        }
        check = ZoneBalanceCheck(machines)
        result = check.run()
        assert result.passed is True
        assert result.message == "deployment is balanced"

    def test_run_with_unbalanced_roles(self):
        machines = {
            "zone1": [
                {"roles": [RoleTags.CONTROL.value, RoleTags.STORAGE.value]},
                {"roles": [RoleTags.CONTROL.value, RoleTags.COMPUTE.value]},
            ],
            "zone2": [
                {"roles": [RoleTags.CONTROL.value, RoleTags.STORAGE.value]},
                {"roles": [RoleTags.CONTROL.value]},
            ],
        }
        check = ZoneBalanceCheck(machines)
        result = check.run()
        assert result.passed is False
        assert result.message and "compute distribution is unbalanced" in result.message


class TestMaasScaleJujuStep:
    def test_is_skip_with_controller_not_found(self, mocker):
        maas_client = mocker.Mock()
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        mocker.patch.object(
            step,
            "get_controller",
            side_effect=ControllerNotFoundException("Controller not found"),
        )
        result = step.is_skip()
        assert result.result_type == ResultType.FAILED
        assert result.message == f"Controller {controller} not found"

    def test_is_skip_with_no_registered_machines(self, mocker):
        maas_client = mocker.Mock()
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        mocker.patch.object(
            step, "get_controller", return_value={"controller-machines": None}
        )
        result = step.is_skip()
        assert result.result_type == ResultType.FAILED
        assert result.message == f"Controller {controller} has no machines registered."

    def _controller_machines_raw(self) -> list[dict]:
        return [
            {
                "hostname": "c-1",
                "blockdevice_set": [],
                "interface_set": [],
                "zone": {"name": "default"},
                "tag_names": [RoleTags.JUJU_CONTROLLER.value],
                "status_name": "deployed",
                "cpu_count": 4,
                "memory": 32768,
            },
            {
                "hostname": "c-1",
                "blockdevice_set": [],
                "interface_set": [],
                "zone": {"name": "default"},
                "tag_names": [RoleTags.JUJU_CONTROLLER.value],
                "status_name": "deployed",
                "cpu_count": 4,
                "memory": 32768,
            },
        ]

    def test_is_skip_with_already_correct_number_of_controllers(self, mocker):
        maas_client = mocker.Mock(
            list_machines=Mock(return_value=self._controller_machines_raw())
        )
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        step.n = 2
        mocker.patch.object(
            step, "get_controller", return_value={"controller-machines": [1, 2]}
        )
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_cannot_scale_down_controllers(self, mocker):
        maas_client = mocker.Mock(
            list_machines=Mock(return_value=self._controller_machines_raw())
        )
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        step.n = 1
        mocker.patch.object(
            step, "get_controller", return_value={"controller-machines": [1, 2]}
        )
        result = step.is_skip()
        assert result.result_type == ResultType.FAILED
        assert result.message == f"Can't scale down controllers from 2 to {step.n}."

    def test_is_skip_with_insufficient_juju_controllers(self, mocker):
        maas_client = mocker.Mock()
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        step.n = 3
        mocker.patch.object(
            step, "get_controller", return_value={"controller-machines": [1, 2]}
        )
        mocker.patch("sunbeam.commands.maas.list_machines", return_value=[1, 2])
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_completed(self, mocker):
        maas_client = mocker.Mock()
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        step.n = 3
        mocker.patch.object(
            step, "get_controller", return_value={"controller-machines": [1, 2]}
        )
        mocker.patch("sunbeam.commands.maas.list_machines", return_value=[1, 2, 3])
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED
