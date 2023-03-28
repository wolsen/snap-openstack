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
import logging
import json
from dataclasses import asdict, dataclass
from functools import wraps
from pathlib import Path
from typing import Awaitable, List, Optional, TypeVar, cast

import yaml
from juju.application import Application
from juju.controller import Controller
from juju.model import Model

from sunbeam.clusterd.client import Client as clusterClient

LOG = logging.getLogger(__name__)
CONTROLLER_MODEL = "admin/controller"
# Note(gboutry): pylibjuju get_model does not support user/model
MODEL = CONTROLLER_MODEL.split("/")[1]
CONTROLLER = "sunbeam-controller"
JUJU_CONTROLLER_KEY = "JujuController"
ACCOUNT_FILE = "account.yaml"


T = TypeVar("T")


def run_sync(coro: Awaitable[T]) -> T:
    """Helper to run coroutines synchronously."""
    result = asyncio.get_event_loop().run_until_complete(coro)
    return cast(T, result)


class JujuException(Exception):
    """Main juju exception, to be subclassed."""

    pass


class ControllerNotFoundException(JujuException):
    """Raised when controller is missing."""

    pass


class ModelNotFoundException(JujuException):
    """Raised when model is missing."""

    pass


class MachineNotFoundException(JujuException):
    """Raised when machine is missing from model."""

    pass


class JujuAccountNotFound(JujuException):
    """Raised when account in snap's user_data is missing."""

    pass


class ApplicationNotFoundException(JujuException):
    """Raised when application is missing from model."""

    pass


@dataclass
class JujuAccount:
    user: str
    password: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def load(cls, data_location: Path) -> "JujuAccount":
        data_file = data_location / ACCOUNT_FILE
        try:
            with data_file.open() as file:
                return JujuAccount(**yaml.safe_load(file))
        except FileNotFoundError as e:
            raise JujuAccountNotFound() from e

    def write(self, data_location: Path):
        data_file = data_location / ACCOUNT_FILE
        if not data_file.exists():
            data_file.touch()
        data_file.chmod(0o660)
        with data_file.open("w") as file:
            yaml.safe_dump(self.to_dict(), file)


@dataclass
class JujuController:
    api_endpoints: List[str]
    ca_cert: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def load(cls, client: clusterClient) -> "JujuController":
        controller = client.cluster.get_config(JUJU_CONTROLLER_KEY)
        return JujuController(**json.loads(controller))

    def write(self, client: clusterClient):
        client.cluster.update_config(JUJU_CONTROLLER_KEY, json.dumps(self.to_dict()))


def controller(func):
    """Automatically set up controller."""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        if self.controller is None:
            client = clusterClient()
            juju_controller = JujuController.load(client)

            account = JujuAccount.load(self.data_location)

            self.controller = Controller()
            await self.controller.connect(
                endpoint=juju_controller.api_endpoints,
                cacert=juju_controller.ca_cert,
                username=account.user,
                password=account.password,
            )
        return await func(self, *args, **kwargs)

    return wrapper


class JujuHelper:
    """Helper function to manage Juju apis through pylibjuju."""

    def __init__(self, data_location: Path):
        self.data_location = data_location
        self.controller = None

    @controller
    async def get_model(self, model: str) -> Model:
        """Fetch model.

        :model: Name of the model
        """
        try:
            return await self.controller.get_model(model)
        except Exception as e:
            if "HTTP 400" in str(e):
                raise ModelNotFoundException
            raise e

    @controller
    async def get_application(self, name: str, model: str) -> Application:
        """Fetch application in model.

        :name: Application name
        :model: Name of the model where the application is located
        """
        model_impl = await self.get_model(model)
        return model_impl.applications.get(name)

    @controller
    async def add_unit(
        self,
        name: str,
        model: str,
        machine: Optional[str] = None,
    ):
        """Add unit to application, can be optionnally placed on a machine.

        :name: Application name
        :model: Name of the model where the application is located
        :machine: Machine ID to place the unit on, optional
        """

        model_impl = await self.get_model(model)

        application = model_impl.applications.get(name)

        if application is None:
            raise ApplicationNotFoundException(
                f"Application {name} is missing from model {model}"
            )

        # add_unit waits for unit to be added to model, but does not check status
        await application.add_unit(1, machine)

    @controller
    async def remove_unit(self, name: str, unit: str, model: str):
        """Remove unit from application.

        :name: Application name
        :unit: Unit tag
        :model: Name of the model where the application is located
        """

        model_impl = await self.get_model(model)

        application = model_impl.applications.get(name)

        if application is None:
            raise ApplicationNotFoundException(
                f"Application {name} is missing from model {model}"
            )

        await application.destroy_unit(unit)

    @controller
    async def wait_application_ready(
        self,
        name: str,
        model: str,
        accepted_status: Optional[List[str]] = None,
        timeout: Optional[int] = None,
    ):
        """Block execution until application is ready
        The function early exits if the application is missing from the model

        :name: Name of the application to wait for
        :model: Name of the model where the application is located
        :accepted status: List of status acceptable to exit the waiting loop, default:
            ["active"]
        :timeout: Waiting timeout in seconds
        """
        if accepted_status is None:
            accepted_status = ["active"]

        model_impl = await self.get_model(model)
        application = model_impl.applications.get(name)

        if application is None:
            LOG.debug(f"Application {name} is missing from model {model}")
            return

        LOG.debug(f"Application {name} is in status: {application.status}")

        await model_impl.block_until(
            lambda: model_impl.applications[name].status in accepted_status,
            timeout=timeout,
        )

    @controller
    async def wait_until_active(
        self,
        model: str,
        timeout: Optional[int] = None,
    ) -> None:
        """Wait for all units in model to reach active status

        :model: Name of the model to wait for readiness
        :timeout: Waiting timeout in seconds
        """
        model_impl = await self.get_model(model)

        await model_impl.block_until(
            lambda: all(
                unit.workload_status == "active"
                for application in model_impl.applications.values()
                for unit in application.units
            ),
            timeout=timeout,
        )
