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
from juju.application import Application
from juju.model import Model
from juju.unit import Unit

import sunbeam.jobs.juju as juju


@pytest.fixture
def applications() -> dict[str, Application]:
    mock = MagicMock()
    app_dict = {
        "microk8s": AsyncMock(status="active"),
        "macrok8s": AsyncMock(status="unknown"),
    }
    mock.get.side_effect = app_dict.get
    mock.__getitem__.side_effect = app_dict.__getitem__
    return mock


@pytest.fixture
def units() -> dict[str, Unit]:
    mock = MagicMock()
    unit_dict = {
        "microk8s/0": AsyncMock(
            agent_status="idle",
            workload_status="active",
        ),
        "microk8s/1": AsyncMock(
            agent_status="unknown",
            workload_status="unknown",
        ),
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


test_data_microk8s = [
    ("wait_application_ready", "microk8s", "application 'microk8s'", [["blocked"]]),
    (
        "wait_unit_ready",
        "microk8s/0",
        "unit 'microk8s/0'",
        [{"agent": "idle", "workload": "blocked"}],
    ),
    ("wait_until_active", "control-plane", "model 'control-plane'", []),
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
    if "until" in method:
        model.all_units_idle.return_value = True
        await getattr(jhelper, method)(entity)
    else:
        await getattr(jhelper, method)(entity, "control-plane")
    assert model.block_until.call_count == 1
    assert model.block_until.result is True


@pytest.mark.asyncio
@pytest.mark.parametrize("method,entity,error,args", test_data_microk8s)
async def test_jhelper_wait_application_ready_timeout(
    jhelper: juju.JujuHelper, model: Model, method: str, entity: str, error: str, args
):
    model.all_units_idle.return_value = False
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
