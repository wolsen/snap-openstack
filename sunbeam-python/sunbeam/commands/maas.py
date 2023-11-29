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
import logging
import ssl
from pathlib import Path
from typing import Optional, TypedDict

import yaml
from maas.client import bones, connect
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.jobs.common import SHARE_PATH, BaseStep, Result, ResultType

LOG = logging.getLogger(__name__)
console = Console()

MAAS_CONFIG = "maas.yaml"


class MaasDeployment(TypedDict):
    name: str
    token: str
    url: str
    resource_pool: str


class MaasConfig(TypedDict):
    active: str
    deployments: list[MaasDeployment]


class MaasClient:
    """Facade to MAAS APIs."""

    def __init__(self, url: str, token: str):
        self._client = connect(url, apikey=token)

    def get_resource_pool(self, name: str) -> object:
        """Fetch resource pool from MAAS."""
        return self._client.resource_pools.get(name)  # type: ignore


def maas_path(snap: Snap) -> Path:
    """Path to MAAS deployments configuration."""
    openstack = snap.paths.real_home / SHARE_PATH
    openstack.mkdir(parents=True, exist_ok=True)
    path = snap.paths.real_home / SHARE_PATH / MAAS_CONFIG
    if not path.exists():
        path.touch(0o600)
        path.write_text("{}")
    return path


def maas_config(path: Path) -> MaasConfig:
    """Read MAAS deployments configuration."""
    with path.open() as fd:
        data = yaml.safe_load(fd)
    return data


def add_deployment(path: Path, new_deployment: MaasDeployment) -> None:
    """Add MAAS deployment to configuration."""
    config = maas_config(path)
    deployments = config.get("deployments", [])
    deployments.append(new_deployment)
    config["deployments"] = deployments
    config["active"] = new_deployment["name"]
    with path.open("w") as fd:
        yaml.safe_dump(config, fd)
    path.chmod(0o600)


def switch_deployment(path: Path, name: str) -> None:
    """Switch active deployment."""
    config = maas_config(path)
    if config.get("active") == name:
        return
    for deployment in config.get("deployments", []):
        if deployment["name"] == name:
            break
    else:
        raise ValueError(f"Deployment {name} not found in MAAS deployments.")
    config["active"] = name
    with path.open("w") as fd:
        yaml.safe_dump(config, fd)


def list_deployments(path: Path) -> dict:
    config = maas_config(path)
    deployments = [
        {
            "name": deployment["name"],
            "url": deployment["url"],
            "resource_pool": deployment["resource_pool"],
        }
        for deployment in config.get("deployments", [])
    ]
    return {"active": config.get("active"), "deployments": deployments}


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
        config = maas_config(self.path)
        if self.deployment in config:
            return Result(
                ResultType.FAILED, f"Deployment {self.deployment} already exists."
            )

        current_deployments = set()
        for deployment in config.get("deployments", []):
            current_deployments.add((deployment["url"], deployment["resource_pool"]))

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
            resource_pool=self.resource_pool,
        )
        add_deployment(self.path, data)
        return Result(ResultType.COMPLETED)
