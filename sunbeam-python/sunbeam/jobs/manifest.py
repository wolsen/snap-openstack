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

import copy
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import Field
from pydantic.dataclasses import dataclass
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.clusterd.client import Client as clusterClient
from sunbeam.clusterd.service import ManifestItemNotFoundException
from sunbeam.jobs.common import BaseStep, Result, ResultType, Status
from sunbeam.jobs.plugin import PluginManager
from sunbeam.versions import (
    CHARM_MANIFEST_TFVARS_MAP,
    MANIFEST_CHARM_VERSIONS,
    TERRAFORM_DIR_NAMES,
)

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
    terraform: Optional[Dict[str, TerraformManifest]] = None

    @classmethod
    def load(cls, manifest_file: Path, on_default: bool = False) -> "Manifest":
        """Load the manifest with the provided file input"""
        if on_default:
            return cls.load_on_default()

        with manifest_file.open() as file:
            return Manifest(**yaml.safe_load(file))

    @classmethod
    def load_latest_from_clusterdb(cls, on_default: bool = False) -> "Manifest":
        """Load the latest manifest from clusterdb

        If on_default is True, load this manifest data over the default
        values.
        """
        if on_default:
            return cls.load_latest_from_clusterdb_on_default()

        try:
            manifest_latest = clusterClient().cluster.get_latest_manifest()
            return Manifest(**yaml.safe_load(manifest_latest.get("data")))
        except ManifestItemNotFoundException as e:
            LOG.debug(f"Error in getting latest manifest from cluster DB: {str(e)}")
            return Manifest()

    @classmethod
    def load_on_default(cls, manifest_file: Path) -> "Manifest":
        """Load manifest and override the default manifest"""
        with manifest_file.open() as file:
            override = yaml.safe_load(file)
            default = cls.get_default_manifest_as_dict()
            utils.merge_dict(default, override)
            return Manifest(**default)

    @classmethod
    def load_latest_from_clusterdb_on_default(cls) -> "Manifest":
        """Load the latest manifest from clusterdb"""
        default = cls.get_default_manifest_as_dict()
        try:
            manifest_latest = clusterClient().cluster.get_latest_manifest()
            override = yaml.safe_load(manifest_latest.get("data"))
        except ManifestItemNotFoundException as e:
            LOG.debug(f"Error in getting latest manifest from cluster DB: {str(e)}")
            override = {}

        utils.merge_dict(default, override)
        m = Manifest(**default)
        LOG.debug(f"Latest applied manifest with defaults: {m}")
        return m

    @classmethod
    def get_default_manifest_as_dict(cls) -> dict:
        snap = Snap()
        m = {"juju": None, "charms": {}, "terraform": {}}
        m["charms"] = {
            charm: {"channel": channel}
            for charm, channel in MANIFEST_CHARM_VERSIONS.items()
        }
        m["terraform"] = {
            tfplan: {"source": Path(snap.paths.snap / "etc" / tfplan_dir)}
            for tfplan, tfplan_dir in TERRAFORM_DIR_NAMES.items()
        }

        # Update manifests from plugins
        m_plugin = PluginManager().get_all_plugin_manifests()
        utils.merge_dict(m, m_plugin)

        return copy.deepcopy(m)

    @classmethod
    def get_default_manifest(cls) -> "Manifest":
        return Manifest(**cls.get_default_manifest_as_dict())

    """
    # field_validator supported only in pydantix 2.x
    @field_validator("terraform", "mode_after")
    def validate_terraform(cls, terraform):
        if terraform:
            tf_keys = list(terraform.keys())
            if not set(tf_keys) <= set(VALID_TERRAFORM_PLANS):
                raise ValueError(
                    f"Terraform keys should be one of {VALID_TERRAFORM_PLANS}"
                )

        return terraform
    """

    def validate_terraform_keys(self, default_manifest: dict):
        if self.terraform:
            tf_keys = set(self.terraform.keys())
            all_tfplans = default_manifest.get("terraform", {}).keys()
            if not tf_keys <= all_tfplans:
                raise ValueError(f"Terraform keys should be one of {all_tfplans} ")

    def __post_init__(self):
        LOG.debug("Calling __post__init__")
        manifest_dict = self.get_default_manifest_as_dict()
        # Add custom validations
        self.validate_terraform_keys(manifest_dict)

    def get_tfvars(self, plan: str) -> dict:
        tfvars = {}
        tfvar_map = copy.deepcopy(CHARM_MANIFEST_TFVARS_MAP)
        tfvar_map_plugin = PluginManager().get_all_plugin_manfiest_tfvar_map()
        utils.merge_dict(tfvar_map, tfvar_map_plugin)

        for charm, value in tfvar_map.get(plan, {}).items():
            manifest_charm = asdict(self.charms.get(charm))
            for key, val in value.items():
                if manifest_charm.get(key):
                    tfvars[val] = manifest_charm.get(key)

        return tfvars


class AddManifestStep(BaseStep):
    """Add Manifest file to cluster database"""

    def __init__(self, manifest: Optional[Path] = None):
        super().__init__("Write Manifest to database", "Writing Manifest to database")
        # Write EMPTY_MANIFEST if manifest not provided
        self.manifest = manifest
        self.client = clusterClient()

    def run(self, status: Optional[Status] = None) -> Result:
        """Write manifest to cluster db"""
        try:
            if self.manifest:
                with self.manifest.open("r") as file:
                    data = yaml.safe_load(file)
                    id = self.client.cluster.add_manifest(data=yaml.safe_dump(data))
            else:
                id = self.client.cluster.add_manifest(
                    data=yaml.safe_dump(EMPTY_MANIFEST)
                )

            return Result(ResultType.COMPLETED, id)
        except Exception as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
