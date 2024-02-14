# Copyright (c) 2024 Canonical Ltd.
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

import logging

import pydantic
import snaphelpers

from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.commands.clusterd import CLUSTERD_PORT
from sunbeam.jobs.deployment import Deployment
from sunbeam.jobs.juju import JujuAccount, JujuAccountNotFound, JujuController

LOG = logging.getLogger(__name__)
LOCAL_TYPE = "local"


class LocalDeployment(Deployment):
    name: str = "local"
    url: str = "local"
    type: str = LOCAL_TYPE
    _client: Client | None = pydantic.PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        if self.juju_account is None:
            self.juju_account = self._load_juju_account()
        if self.juju_controller is None:
            self.juju_controller = self._load_juju_controller()

    def _load_juju_account(self) -> JujuAccount | None:
        try:
            juju_account = JujuAccount.load(snaphelpers.Snap().paths.user_data)
            LOG.debug(f"Local account found: {juju_account.user}")
            return juju_account
        except JujuAccountNotFound:
            LOG.debug("No juju account found", exc_info=True)
            return None

    def _load_juju_controller(self) -> JujuController | None:
        try:
            return JujuController.load(self.get_client())
        except ConfigItemNotFoundException:
            LOG.debug("No juju controller found", exc_info=True)
            return None
        except ClusterServiceUnavailableException:
            LOG.debug("Clusterd service unavailable", exc_info=True)
            return None

    def reload_juju_credentials(self):
        self.juju_account = self._load_juju_account()
        self.juju_controller = self._load_juju_controller()

    @property
    def infrastructure_model(self) -> str:
        """Return the infrastructure model name."""
        return "controller"

    def get_client(self) -> Client:
        """Return a client for the deployment."""
        if self._client is None:
            self._client = Client.from_socket()
        return self._client

    def get_clusterd_http_address(self) -> str:
        """Return the address of the clusterd server."""
        local_ip = utils.get_local_ip_by_default_route()
        address = f"https://{local_ip}:{CLUSTERD_PORT}"
        return address
