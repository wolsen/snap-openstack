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

from sunbeam.commands.maas import AddMaasDeployment
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
