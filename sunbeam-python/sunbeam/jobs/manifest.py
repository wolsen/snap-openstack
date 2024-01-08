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

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import Field, ValidationError
from pydantic.dataclasses import dataclass

from sunbeam.clusterd.client import Client as clusterClient
from sunbeam.jobs.common import BaseStep, Result, ResultType, Status

LOG = logging.getLogger(__name__)
EMPTY_MANIFEST = """charms: {}
terraform-plans: {}
"""


@dataclass
class JujuManifest:
    bootstrap_args: List[str] = Field(
        alias="bootstrap-args", description="Extra args for juju bootstrap"
    )


@dataclass
class CharmsManifest:
    channel: Optional[str] = Field(default=None, description="Channel for the charm")
    revision: Optional[int] = Field(
        default=None, description="Revision number of the charm"
    )
    rocks: Optional[Dict[str, str]] = Field(
        default=None, description="Rock images for the charm"
    )
    config: Optional[Dict[str, Any]] = Field(
        default=None, description="Config options of the charm"
    )
    source: Optional[Path] = Field(default=None, description="Local charm bundle path")


@dataclass
class TerraformManifest:
    source: Path = Field(description="Path to Terraform plan")


@dataclass
class Manifest:
    juju: Optional[JujuManifest] = None
    charms: Optional[Dict[str, CharmsManifest]] = None
    terraform_plans: Optional[Dict[str, TerraformManifest]] = Field(
        default=None, alias="terraform-plans"
    )

    @classmethod
    def load(cls, manifest_file: Path) -> "Manifest":
        try:
            with manifest_file.open() as file:
                return Manifest(**yaml.safe_load(file))
        except FileNotFoundError as e:
            raise e
        except ValidationError as e:
            raise e

    @classmethod
    def load_latest_from_cluserdb(cls) -> "Manifest":
        manifest_latest = clusterClient().cluster.get_latest_manifest()
        return Manifest(**manifest_latest)


class AddManifestStep(BaseStep):
    """Add Manifest file to cluster database"""

    def __init__(self, manifest: Path):
        super().__init__("Write Manifest to database", "Writing Manifest to database")
        self.manifest = manifest
        self.client = clusterClient()

    def run(self, status: Optional[Status] = None) -> Result:
        """Write manifest to cluster db"""
        try:
            with self.manifest.open("r") as file:
                data = yaml.safe_load(file)
                id = self.client.cluster.add_manifest(data=yaml.safe_dump(data))
                return Result(ResultType.COMPLETED, id)
        except Exception as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
