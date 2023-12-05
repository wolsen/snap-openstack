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

"""MAAS management."""

import builtins
import collections
import enum
import logging
import ssl
from pathlib import Path
from typing import Optional, TypeGuard

from maas.client import bones, connect
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.commands.deployment import (
    Deployment,
    DeploymentType,
    add_deployment,
    deployment_config,
    deployment_path,
    get_active_deployment,
)
from sunbeam.jobs.common import BaseStep, Result, ResultType

LOG = logging.getLogger(__name__)
console = Console()

MAAS_CONFIG = "maas.yaml"


class MaasDeployment(Deployment):
    token: str
    resource_pool: str


def is_maas_deployment(deployment: Deployment) -> TypeGuard[MaasDeployment]:
    """Check if deployment is a MAAS deployment."""
    return deployment["type"] == DeploymentType.MAAS.value


class RoleTags(enum.Enum):
    CONTROL = "control"
    COMPUTE = "compute"
    STORAGE = "storage"
    JUJU = "juju"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class StorageTags(enum.Enum):
    CEPH = "ceph"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class MaasClient:
    """Facade to MAAS APIs."""

    def __init__(self, url: str, token: str, resource_pool: Optional[str] = None):
        self._client = connect(url, apikey=token)
        self.resource_pool = resource_pool

    def get_resource_pool(self, name: str) -> object:
        """Fetch resource pool from MAAS."""
        return self._client.resource_pools.get(name)  # type: ignore

    def list_machines(self) -> list[dict]:
        """List machines."""
        kwargs = {}
        if self.resource_pool:
            kwargs["pool"] = self.resource_pool
        try:
            return self._client.machines.list.__self__._handler.read(**kwargs)  # type: ignore # noqa
        except bones.CallError as e:
            if "No such pool" in str(e):
                raise ValueError(f"Resource pool {self.resource_pool!r} not found.")
            raise e

    def get_machine(self, machine: str) -> dict:
        """Get machine."""
        kwargs = {
            "hostname": machine,
        }
        if self.resource_pool:
            kwargs["pool"] = self.resource_pool
        machines = self._client.machines.list.__self__._handler.read(**kwargs)  # type: ignore # noqa
        if len(machines) == 0:
            raise ValueError(f"Machine {machine!r} not found.")
        if len(machines) > 1:
            raise ValueError(f"Machine {machine!r} not unique.")
        return machines[0]

    @classmethod
    def active(cls, snap: Snap) -> "MaasClient":
        """Return client connected to active deployment."""
        path = deployment_path(snap)
        deployment = get_active_deployment(path)
        if not is_maas_deployment(deployment):
            raise ValueError("Active deployment is not a MAAS deployment.")
        return cls(
            deployment["url"],
            deployment["token"],
            deployment["resource_pool"],
        )


def list_machines(client: MaasClient) -> list[dict]:
    """List machines in deployment, return consumable list of dicts."""
    machines_raw = client.list_machines()

    machines = []
    for machine in machines_raw:
        machines.append(
            {
                "hostname": machine["hostname"],
                "roles": list(
                    set(machine["tag_names"]).intersection(RoleTags.values())
                ),
                "zone": machine["zone"]["name"],
                "status": machine["status_name"],
            }
        )
    return machines


def get_machine(client: MaasClient, machine: str) -> dict:
    """Get machine in deployment, return consumable dict."""
    machine_raw = client.get_machine(machine)

    storage_tags = collections.Counter()
    for blockdevice in machine_raw["blockdevice_set"]:
        storage_tags.update(set(blockdevice["tags"]).intersection(StorageTags.values()))

    spaces = []
    for interface in machine_raw["interface_set"]:
        spaces.append(interface["vlan"]["space"])

    return {
        "hostname": machine_raw["hostname"],
        "roles": list(set(machine_raw["tag_names"]).intersection(RoleTags.values())),
        "zone": machine_raw["zone"]["name"],
        "status": machine_raw["status_name"],
        "storage": dict(storage_tags),
        "spaces": list(set(spaces)),
    }


class AddMaasDeployment(BaseStep):
    def __init__(
        self,
        deployment: str,
        token: str,
        url: str,
        resource_pool: str,
        config_path: Path,
    ) -> None:
        super().__init__(
            "Add MAAS-backed deployment",
            "Adding MAAS-backed deployment for OpenStack usage",
        )
        self.deployment = deployment
        self.token = token
        self.url = url
        self.resource_pool = resource_pool
        self.path = config_path

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Check if deployment is already added."""
        config = deployment_config(self.path)
        if self.deployment in config:
            return Result(
                ResultType.FAILED, f"Deployment {self.deployment} already exists."
            )

        current_deployments = set()
        for deployment in config.get("deployments", []):
            if is_maas_deployment(deployment):
                current_deployments.add(
                    (
                        deployment["url"],
                        deployment["resource_pool"],
                    )
                )

        if (self.url, self.resource_pool) in current_deployments:
            return Result(
                ResultType.FAILED,
                "Deployment with same url and resource pool already exists.",
            )

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Check MAAS is working, Resource Pool exists, write to local configuration."""
        try:
            client = MaasClient(self.url, self.token)
            _ = client.get_resource_pool(self.resource_pool)
        except ValueError as e:
            LOG.debug("Failed to connect to maas", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except bones.CallError as e:
            if e.status == 401:
                LOG.debug("Unauthorized", exc_info=True)
                return Result(
                    ResultType.FAILED,
                    "Unauthorized, check your api token has necessary permissions.",
                )
            elif e.status == 404:
                LOG.debug("Resource pool not found", exc_info=True)
                return Result(
                    ResultType.FAILED,
                    f"Resource pool {self.resource_pool!r} not"
                    " found in given MAAS URL.",
                )
            LOG.debug("Unknown error", exc_info=True)
            return Result(ResultType.FAILED, f"Unknown error, {e}")
        except Exception as e:
            match type(e.__cause__):
                case builtins.ConnectionRefusedError:
                    LOG.debug("Connection refused", exc_info=True)
                    return Result(
                        ResultType.FAILED, "Connection refused, is the url correct?"
                    )
                case ssl.SSLError:
                    LOG.debug("SSL error", exc_info=True)
                    return Result(
                        ResultType.FAILED, "SSL error, failed to connect to remote."
                    )
            LOG.info("Exception info", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        data = MaasDeployment(
            name=self.deployment,
            token=self.token,
            url=self.url,
            type=DeploymentType.MAAS.value,
            resource_pool=self.resource_pool,
        )
        add_deployment(self.path, data)
        return Result(ResultType.COMPLETED)
