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
from pathlib import Path
from ssl import SSLError
from unittest.mock import Mock

import pytest
from maas.client.bones import CallError

from sunbeam.commands.maas import (
    AddMaasDeployment,
    DeploymentRolesCheck,
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


class TestAddMaasDeployment:
    @pytest.fixture
    def add_maas_deployment(self):
        return AddMaasDeployment(
            deployment="test_deployment",
            token="test_token",
            url="test_url",
            resource_pool="test_resource_pool",
            config_path=Path("test_path"),
        )

    def test_is_skip_with_existing_deployment(self, add_maas_deployment, mocker):
        mocker.patch(
            "sunbeam.commands.maas.deployment_config",
            return_value={
                "active": "test_deployment",
                "deployments": [
                    {
                        "name": "test_deployment",
                        "type": "maas",
                        "url": "test_url",
                        "resource_pool": "test_resource_pool",
                    }
                ],
            },
        )
        result = add_maas_deployment.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_with_existing_url_and_resource_pool(
        self, add_maas_deployment, mocker
    ):
        mocker.patch(
            "sunbeam.commands.maas.deployment_config",
            return_value={
                "deployments": [
                    {
                        "type": "maas",
                        "url": "test_url",
                        "resource_pool": "test_resource_pool",
                    }
                ]
            },
        )
        result = add_maas_deployment.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_with_no_existing_deployment(self, add_maas_deployment, mocker):
        mocker.patch("sunbeam.commands.maas.deployment_config", return_value={})
        result = add_maas_deployment.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_successful_connection(self, add_maas_deployment, mocker):
        mocker.patch("sunbeam.commands.maas.MaasClient", autospec=True)
        mocker.patch("sunbeam.commands.maas.add_deployment", autospec=True)
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
                **{network: "alpha" for network in Networks.values()},
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
            return_value={network: "alpha" for network in Networks.values()},
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
