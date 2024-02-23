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

import enum
from typing import Type, TypeGuard

import pydantic

from sunbeam.clusterd.client import Client
from sunbeam.jobs.deployment import Deployment

MAAS_TYPE = "maas"
MAAS_PUBLIC_IP_RANGE = "sunbeam-public-api"
MAAS_INTERNAL_IP_RANGE = "sunbeam-internal-api"


class Networks(enum.Enum):
    PUBLIC = "public"
    STORAGE = "storage"
    STORAGE_CLUSTER = "storage-cluster"
    INTERNAL = "internal"
    DATA = "data"
    MANAGEMENT = "management"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class RoleTags(enum.Enum):
    CONTROL = "control"
    COMPUTE = "compute"
    STORAGE = "storage"
    JUJU_CONTROLLER = "juju-controller"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


ROLE_NETWORK_MAPPING = {
    RoleTags.CONTROL: [
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.PUBLIC,
        Networks.STORAGE,
    ],
    RoleTags.COMPUTE: [
        Networks.DATA,
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.STORAGE,
    ],
    RoleTags.STORAGE: [
        Networks.DATA,
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.STORAGE,
        Networks.STORAGE_CLUSTER,
    ],
    RoleTags.JUJU_CONTROLLER: [
        Networks.MANAGEMENT,
    ],
}


class StorageTags(enum.Enum):
    CEPH = "ceph"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class NicTags(enum.Enum):
    COMPUTE = "compute"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class MaasDeployment(Deployment):
    type: str = MAAS_TYPE
    token: str
    resource_pool: str
    network_mapping: dict[str, str | None] = {}
    clusterd_address: str | None = None
    _client: Client | None = pydantic.PrivateAttr(default=None)

    @property
    def controller(self) -> str:
        """Return controller name."""
        return self.name + "-controller"

    @pydantic.validator("type")
    def type_validator(cls, v: str, values: dict) -> str:
        if v != MAAS_TYPE:
            raise ValueError("Deployment type must be MAAS.")
        return v

    @classmethod
    def import_step(cls) -> Type:
        """Return a step for importing a deployment.

        This step will be used to make sure the deployment is valid.
        The step must take as constructor arguments: DeploymentsConfig, Deployment.
        The Deployment must be of the type that the step is registered for.
        """
        from sunbeam.provider.maas.commands import AddMaasDeployment

        return AddMaasDeployment

    @property
    def infrastructure_model(self) -> str:
        """Return the infrastructure model name."""
        return "openstack-machines"

    def get_client(self) -> Client:
        """Return a client for the deployment."""
        if self.clusterd_address is None:
            raise ValueError("Clusterd address not set.")
        if self._client is None:
            self._client = Client.from_http(self.clusterd_address)
        return self._client

    def get_clusterd_http_address(self) -> str:
        """Return the address of the clusterd server."""
        if self.clusterd_address is None:
            raise ValueError("Clusterd address not set.")
        return self.clusterd_address


def is_maas_deployment(deployment: Deployment) -> TypeGuard[MaasDeployment]:
    """Check if deployment is a MAAS deployment."""
    return isinstance(deployment, MaasDeployment)
