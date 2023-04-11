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
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
import yaml
from juju.application import Application
from juju.model import Model
from juju.unit import Unit

import sunbeam.jobs.juju as juju

kubeconfig_yaml = """
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUREekNDQWZlZ0F3SUJBZ0lVSDh2MmtKZDE0TEs4VWIrM1RmUGVUY21pMWNrd0RRWUpLb1pJaHZjTkFRRUwKQlFBd0Z6RVZNQk1HQTFVRUF3d01NVEF1TVRVeUxqRTRNeTR4TUI0WERUSXpNRFF3TkRBMU1Ua3lOVm9YRFRNegpNRFF3TVRBMU1Ua3lOVm93RnpFVk1CTUdBMVVFQXd3TU1UQXVNVFV5TGpFNE15NHhNSUlCSWpBTkJna3Foa2lHCjl3MEJBUUVGQUFPQ0FROEFNSUlCQ2dLQ0FRRUF4RWkwVFhldmJYNFNvZ2VsRW16T0NQU2tYNHloOURCVGd6WFEKQkdJQTF4TDFwZ09mRkNMNzZYSlROSU4rYUNPT1BoVGp6dXoyR3dpR05pMHVBdnZyUGVrN0p0cEliUjg4YjRSQQpZUTRtMTllMU5zVjdwZ2pHL0JEQzVza1dycVpoZTR5ZTZoOXI2OXpKb1l5NEE4eFZLb1MvdElBZkdSejZvaS9uCndpY0ZzKzQyc29icm92MFdyUm5KbFV4eisyVHB2TFA1TW40eUExZHpGV0RLMTVCemVHa1YyYTVDeHBqcFBBTE4KVzUwVWlvSittbHBmTmwvYzZKWmFaZDR4S1NxclppU2dCY3BOQlhvWjJYVHpDOVNJTFF5RGZpZUpVNWxOcEIwSgpvSUphT0UvOTNseGp1bUdsSlRLSS9ucmpYM241UDFyaFFlWTNxV2p5S21ZNlFucjRqUUlEQVFBQm8xTXdVVEFkCkJnTlZIUTRFRmdRVU0yVTBMSTZtcGFaOTVkTnlIRGs1ZlZCck5ISXdId1lEVlIwakJCZ3dGb0FVTTJVMExJNm0KcGFaOTVkTnlIRGs1ZlZCck5ISXdEd1lEVlIwVEFRSC9CQVV3QXdFQi96QU5CZ2txaGtpRzl3MEJBUXNGQUFPQwpBUUVBZzZITWk4eTQrSENrOCtlb1FuamlmOHd4MytHVDZFNk02SWdRWWRvSFJjYXNYZ0JWLzd6OVRHQnpNeG1aCmdrL0Fnc08yQitLUFh3NmdQZU1GL1JLMjhGNlovK0FjYWMzdUtjT1N1WUJiL2lRKzI1cU9BazZaTStoSTVxMWQKUm1uVzBIQmpzNmg1bVlDODJrSVcrWStEYWN5bUx3OTF3S2ptTXlvMnh4OTBRb0IvWnBSVUxiNjVvWmlkcHZEawpOMStleFg4QmhIeE85S0lhMFFvcThVWFdLTjN4anZRb1pVanFieXY1VWFvcjBwbWpKT1NLKzJLMllRSk9FbUxaCkFDdEtzUDNpaU1UTlRXYUpxVjdWUVZaL3dRUVdsQ1h3VFp3WGlicXk0Z0kwb3JrcVNha0gzVFZMblVrRlFKU24KUi8waU1RRVFzQW5kajZBcVhlQml3ZG5aSGc9PQotLS0tLUVORCBDRVJUSUZJQ0FURS0tLS0tCg==  # noqa: E501
    server: https://10.5.1.180:16443
  name: microk8s-cluster
contexts:
- context:
    cluster: microk8s-cluster
    user: admin
  name: microk8s
current-context: microk8s
kind: Config
preferences: {}
users:
- name: admin
  user:
    token: FAKETOKEN
"""


@pytest.fixture
def applications() -> dict[str, Application]:
    mock = MagicMock()
    microk8s_unit_mock = AsyncMock(
        entity_id="microk8s/0",
        agent_status="idle",
        workload_status="active",
    )
    microk8s_unit_mock.is_leader_from_status.return_value = True

    macrok8s_unit_mock = AsyncMock(
        entity_id="macrok8s/0",
        agent_status="idle",
        workload_status="active",
    )
    macrok8s_unit_mock.is_leader_from_status.return_value = False

    app_dict = {
        "microk8s": AsyncMock(status="active", units=[microk8s_unit_mock]),
        "macrok8s": AsyncMock(status="unknown", units=[macrok8s_unit_mock]),
    }
    mock.get.side_effect = app_dict.get
    mock.__getitem__.side_effect = app_dict.__getitem__
    return mock


@pytest.fixture
def units() -> dict[str, Unit]:
    mock = MagicMock()
    microk8s_0_unit_mock = AsyncMock(
        entity_id="microk8s/0",
        agent_status="idle",
        workload_status="active",
    )
    microk8s_0_unit_mock.run_action.return_value = AsyncMock(
        _status="completed",
        results={"exit_code": 0},
    )

    microk8s_1_unit_mock = AsyncMock(
        entity_id="microk8s/1",
        agent_status="unknown",
        workload_status="unknown",
    )
    microk8s_1_unit_mock.run_action.return_value = AsyncMock(
        _status="failed",
        results={"exit_code": 1},
    )

    unit_dict = {
        "microk8s/0": microk8s_0_unit_mock,
        "microk8s/1": microk8s_1_unit_mock,
    }
    mock.get.side_effect = unit_dict.get
    mock.__getitem__.side_effect = unit_dict.__getitem__
    return mock


@pytest.fixture
def model(applications, units) -> Model:
    model = AsyncMock()
    model.units = units
    model.applications = applications
    model.all_units_idle = Mock()

    def test_condition(condition, timeout):
        """False condition raises a timeout"""
        result = condition()
        model.block_until.result = result
        if not result:
            raise asyncio.TimeoutError(f"Timed out after {timeout} seconds")
        return result

    model.block_until.side_effect = test_condition

    model.get_action_output.return_value = "action failed..."

    return model


@pytest.fixture
def jhelper_base(tmp_path: Path) -> juju.JujuHelper:
    jhelper = juju.JujuHelper.__new__(juju.JujuHelper)
    jhelper.data_location = tmp_path
    jhelper.controller = AsyncMock()  # type: ignore
    return jhelper


@pytest.fixture
def jhelper_404(jhelper_base: juju.JujuHelper):
    # pyright: reportGeneralTypeIssues=false
    jhelper_base.controller.get_model.side_effect = Exception("HTTP 400")
    yield jhelper_base
    jhelper_base.controller.get_model.side_effect = None


@pytest.fixture
def jhelper_unknown_error(jhelper_base: juju.JujuHelper):
    # pyright: reportGeneralTypeIssues=false
    jhelper_base.controller.get_model.side_effect = Exception("Unknown error")
    yield jhelper_base
    jhelper_base.controller.get_model.side_effect = None


@pytest.fixture
def jhelper(mocker, jhelper_base: juju.JujuHelper, model):
    jhelper_base.controller.get_model.return_value = model
    yield jhelper_base


@pytest.mark.asyncio
async def test_jhelper_get_clouds(jhelper: juju.JujuHelper):
    await jhelper.get_clouds()
    jhelper.controller.clouds.assert_called_once()


@pytest.mark.asyncio
async def test_jhelper_get_model(jhelper: juju.JujuHelper):
    await jhelper.get_model("control-plane")
    jhelper.controller.get_model.assert_called_with("control-plane")


@pytest.mark.asyncio
async def test_jhelper_get_model_missing(
    jhelper_404: juju.JujuHelper,
):
    with pytest.raises(juju.ModelNotFoundException, match="Model 'missing' not found"):
        await jhelper_404.get_model("missing")


@pytest.mark.asyncio
async def test_jhelper_get_model_unknown_error(
    jhelper_unknown_error: juju.JujuHelper,
):
    with pytest.raises(Exception, match="Unknown error"):
        await jhelper_unknown_error.get_model("control-plane")


@pytest.mark.asyncio
async def test_jhelper_get_unit(jhelper: juju.JujuHelper, units):
    await jhelper.get_unit("microk8s/0", "control-plane")
    units.get.assert_called_with("microk8s/0")


@pytest.mark.asyncio
async def test_jhelper_get_unit_missing(jhelper: juju.JujuHelper):
    name = "mysql/0"
    model = "control-plane"
    with pytest.raises(
        juju.UnitNotFoundException,
        match=f"Unit {name!r} is missing from model {model!r}",
    ):
        await jhelper.get_unit(name, model)


@pytest.mark.asyncio
async def test_jhelper_get_unit_invalid_name(jhelper: juju.JujuHelper):
    with pytest.raises(
        ValueError,
        match=(
            "Name 'microk8s' has invalid format, "
            "should be a valid unit of format application/id"
        ),
    ):
        await jhelper.get_unit("microk8s", "control-plane")


@pytest.mark.asyncio
async def test_jhelper_get_leader_unit(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    app = "microk8s"
    unit = await jhelper.get_leader_unit(app, "control-plane")
    assert unit is not None
    assert applications.get.called_with(app)


@pytest.mark.asyncio
async def test_jhelper_get_leader_unit_missing_application(jhelper: juju.JujuHelper):
    model = "control-plane"
    app = "mysql"
    with pytest.raises(
        juju.ApplicationNotFoundException,
        match=f"Application missing from model: {model!r}",
    ):
        await jhelper.get_leader_unit(app, model)


@pytest.mark.asyncio
async def test_jhelper_get_leader_unit_missing(jhelper: juju.JujuHelper):
    model = "control-plane"
    app = "macrok8s"
    with pytest.raises(
        juju.LeaderNotFoundException,
        match=f"Leader for application {app!r} is missing from model {model!r}",
    ):
        await jhelper.get_leader_unit(app, model)


@pytest.mark.asyncio
async def test_jhelper_get_application(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    app = await jhelper.get_application("microk8s", "control-plane")
    assert app is not None
    assert applications.get.called_with("microk8s")


@pytest.mark.asyncio
async def test_jhelper_get_application_missing(jhelper: juju.JujuHelper):
    model = "control-plane"
    with pytest.raises(
        juju.ApplicationNotFoundException,
        match=f"Application missing from model: {model!r}",
    ):
        await jhelper.get_application("mysql", model)


@pytest.mark.asyncio
async def test_jhelper_add_unit(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    await jhelper.add_unit("microk8s", "control-plane")
    applications["microk8s"].add_unit.assert_called_with(1, None)


@pytest.mark.asyncio
async def test_jhelper_add_unit_to_machine(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    await jhelper.add_unit("microk8s", "control-plane", machine="0")
    applications["microk8s"].add_unit.assert_called_with(1, "0")


@pytest.mark.asyncio
async def test_jhelper_add_unit_to_missing_application(
    jhelper: juju.JujuHelper,
):
    name = "mysql"
    model = "control-plane"
    with pytest.raises(
        juju.ApplicationNotFoundException,
        match=f"Application {name!r} is missing from model {model!r}",
    ):
        await jhelper.add_unit(name, model)


@pytest.mark.asyncio
async def test_jhelper_remove_unit(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    await jhelper.remove_unit("microk8s", "microk8s/0", "control-plane")
    applications["microk8s"].destroy_unit.assert_called_with("microk8s/0")


@pytest.mark.asyncio
async def test_jhelper_remove_unit_missing_application(
    jhelper: juju.JujuHelper,
):
    name = "mysql"
    unit = "mysql/0"
    model = "control-plane"
    with pytest.raises(
        juju.ApplicationNotFoundException,
        match=f"Application {name!r} is missing from model {model!r}",
    ):
        await jhelper.remove_unit(name, unit, model)


@pytest.mark.asyncio
async def test_jhelper_remove_unit_invalid_unit(
    jhelper: juju.JujuHelper,
):
    with pytest.raises(
        ValueError,
        match=(
            "Name 'microk8s' has invalid format, "
            "should be a valid unit of format application/id"
        ),
    ):
        await jhelper.remove_unit("microk8s", "microk8s", "control-plane")


@pytest.mark.asyncio
async def test_jhelper_run_action(jhelper: juju.JujuHelper, units):
    unit = "microk8s/0"
    action_name = "get-action"
    await jhelper.run_action(unit, "control-plane", action_name)
    units.get(unit).run_action.assert_called_once_with(action_name)


@pytest.mark.asyncio
async def test_jhelper_run_action_failed(jhelper: juju.JujuHelper):
    with pytest.raises(
        juju.ActionFailedException,
        match="action failed...",
    ):
        await jhelper.run_action("microk8s/1", "control-plane", "get-action")


@pytest.mark.asyncio
async def test_jhelper_scp_from(jhelper: juju.JujuHelper, units):
    unit = "microk8s/0"
    await jhelper.scp_from(unit, "control-plane", "source", "destination")
    units.get(unit).scp_from.assert_called_once_with("source", "destination")


@pytest.mark.asyncio
async def test_jhelper_add_k8s_cloud(jhelper: juju.JujuHelper):
    kubeconfig = yaml.safe_load(kubeconfig_yaml)
    await jhelper.add_k8s_cloud("microk8s", "microk8s-creds", kubeconfig)


test_data_microk8s = [
    ("wait_application_ready", "microk8s", "application 'microk8s'", [["blocked"]]),
    (
        "wait_unit_ready",
        "microk8s/0",
        "unit 'microk8s/0'",
        [{"agent": "idle", "workload": "blocked"}],
    ),
]

test_data_custom_status = [
    ("wait_application_ready", "macrok8s", ["unknown"]),
    ("wait_unit_ready", "microk8s/1", {"agent": "unknown", "workload": "unknown"}),
]

test_data_missing = [
    ("wait_application_ready", "mysql"),
    ("wait_unit_ready", "mysql/0"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,entity,error,args", test_data_microk8s)
async def test_jhelper_wait_ready(
    jhelper: juju.JujuHelper, model: Model, method: str, entity: str, error: str, args
):
    await getattr(jhelper, method)(entity, "control-plane")
    assert model.block_until.call_count == 1
    assert model.block_until.result is True


@pytest.mark.asyncio
@pytest.mark.parametrize("method,entity,error,args", test_data_microk8s)
async def test_jhelper_wait_application_ready_timeout(
    jhelper: juju.JujuHelper, model: Model, method: str, entity: str, error: str, args
):
    with pytest.raises(
        juju.TimeoutException,
        match=f"Timed out while waiting for {error} to be ready",
    ):
        await getattr(jhelper, method)(entity, "control-plane", *args)
    assert model.block_until.call_count == 1
    assert model.block_until.result is False


@pytest.mark.asyncio
@pytest.mark.parametrize("method,entity,status", test_data_custom_status)
async def test_jhelper_wait_ready_custom_status(
    jhelper: juju.JujuHelper,
    model: Model,
    method: str,
    entity: str,
    status: list | dict,
):
    await getattr(jhelper, method)(entity, "control-plane", accepted_status=status)
    assert model.block_until.call_count == 1
    assert model.block_until.result is True


@pytest.mark.asyncio
@pytest.mark.parametrize("method,entity", test_data_missing)
async def test_jhelper_wait_ready_missing_application(
    jhelper: juju.JujuHelper, model: Model, method: str, entity: str
):
    await getattr(jhelper, method)(entity, "control-plane")
    assert model.block_until.call_count == 0


@pytest.mark.asyncio
async def test_jhelper_wait_until_active(jhelper: juju.JujuHelper, model):
    await jhelper.wait_until_active("control-plane")
    assert model.wait_for_idle.call_count == 1


@pytest.mark.asyncio
async def test_jhelper_wait_until_active_unit_in_error_state(
    jhelper: juju.JujuHelper, model
):
    model.wait_for_idle.side_effect = juju.JujuWaitException("Unit is in error state")

    with pytest.raises(
        juju.JujuWaitException,
        match="Unit is in error state",
    ):
        await jhelper.wait_until_active("control-plane")
    assert model.wait_for_idle.call_count == 1


@pytest.mark.asyncio
async def test_jhelper_wait_until_active_timed_out(jhelper: juju.JujuHelper, model):
    model.wait_for_idle.side_effect = juju.TimeoutException("timed out...")

    with pytest.raises(
        juju.TimeoutException,
        match="timed out...",
    ):
        await jhelper.wait_until_active("control-plane")
    assert model.wait_for_idle.call_count == 1
