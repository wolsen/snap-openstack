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

import enum
from pathlib import Path
from typing import Optional, TypedDict

import yaml
from rich.console import Console
from snaphelpers import Snap

from sunbeam.jobs.common import SHARE_PATH

console = Console()

DEPLOYMENT_CONFIG = SHARE_PATH / "deployment.yaml"


class DeploymentType(enum.Enum):
    LOCAL = "local"
    MAAS = "maas"


class Deployment(TypedDict):
    name: str
    url: str
    type: str


class DeploymentsConfig(TypedDict):
    active: Optional[str]
    deployments: list[Deployment]


def deployment_path(snap: Snap) -> Path:
    """Path to deployments configuration."""
    openstack = snap.paths.real_home / SHARE_PATH
    openstack.mkdir(parents=True, exist_ok=True)
    path = snap.paths.real_home / DEPLOYMENT_CONFIG
    if not path.exists():
        path.touch(0o600)
        path.write_text("{}")
    return path


def deployment_config(path: Path) -> DeploymentsConfig:
    """Read deployments configuration."""
    with path.open() as fd:
        data = yaml.safe_load(fd)
    if data is None:
        data = DeploymentsConfig(active=None, deployments=[])
    return data


def add_deployment(path: Path, new_deployment: Deployment) -> None:
    """Add MAAS deployment to configuration."""
    config = deployment_config(path)
    deployments = config.get("deployments", [])
    deployments.append(new_deployment)
    config["deployments"] = deployments
    config["active"] = new_deployment["name"]
    with path.open("w") as fd:
        yaml.safe_dump(config, fd)
    path.chmod(0o600)


def switch_deployment(path: Path, name: str) -> None:
    """Switch active deployment."""
    config = deployment_config(path)
    if config.get("active") == name:
        return
    for deployment in config.get("deployments", []):
        if deployment["name"] == name:
            break
    else:
        raise ValueError(f"Deployment {name} not found in deployments.")
    config["active"] = name
    with path.open("w") as fd:
        yaml.safe_dump(config, fd)


def list_deployments(path: Path) -> dict:
    config = deployment_config(path)
    deployments = [
        {
            key: value
            for key, value in deployment.items()
            if key in Deployment.__required_keys__
        }
        for deployment in config.get("deployments", [])
    ]
    return {"active": config.get("active"), "deployments": deployments}


def get_deployment(path: Path, name: str) -> Deployment:
    """Get deployment."""
    config = deployment_config(path)
    for deployment in config.get("deployments", []):
        if deployment["name"] == name:
            return deployment
    raise ValueError(f"Deployment {name} not found in deployments.")


def get_active_deployment(path: Path) -> Deployment:
    """Get active deployment."""
    config = deployment_config(path)
    active = config.get("active")
    if not active:
        raise ValueError("No active deployment found.")
    for deployment in config.get("deployments", []):
        if deployment["name"] == active:
            return deployment
    raise ValueError(f"Active deployment {active} not found in configuration.")
