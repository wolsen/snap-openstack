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
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import Field
from pydantic.dataclasses import dataclass
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.clusterd.client import Client as clusterClient
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    ManifestItemNotFoundException,
)
from sunbeam.commands.terraform import TerraformHelper
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    Status,
    read_config,
    update_config,
)
from sunbeam.jobs.plugin import PluginManager
from sunbeam.versions import (
    MANIFEST_ATTRIBUTES_TFVAR_MAP,
    MANIFEST_CHARM_VERSIONS,
    TERRAFORM_DIR_NAMES,
)

LOG = logging.getLogger(__name__)
EMPTY_MANIFEST = """charms: {}
terraform-plans: {}
"""


class MissingTerraformInfoException(Exception):
    """An Exception raised when terraform information is missing in manifest"""

    pass


@dataclass
class JujuManifest:
    # Setting Field alias not supported in pydantic 1.10.0
    # Old version of pydantic is used due to dependencies
    # with older version of paramiko from python-libjuju
    # Newer version of pydantic can be used once the below
    # PR is released
    # https://github.com/juju/python-libjuju/pull/1005
    bootstrap_args: List[str] = Field(
        default=[], description="Extra args for juju bootstrap"
    )


@dataclass
class CharmsManifest:
    channel: Optional[str] = Field(default=None, description="Channel for the charm")
    revision: Optional[int] = Field(
        default=None, description="Revision number of the charm"
    )
    # rocks: Optional[Dict[str, str]] = Field(
    #     default=None, description="Rock images for the charm"
    # )
    config: Optional[Dict[str, Any]] = Field(
        default=None, description="Config options of the charm"
    )
    # source: Optional[Path] = Field(
    #     default=None, description="Local charm bundle path"
    # )


@dataclass
class TerraformManifest:
    source: Path = Field(description="Path to Terraform plan")


@dataclass(config=dict(extra="allow"))
class Manifest:
    juju: Optional[JujuManifest] = None
    charms: Optional[Dict[str, CharmsManifest]] = None
    terraform: Optional[Dict[str, TerraformManifest]] = None

    @classmethod
    def load(cls, manifest_file: Path, include_defaults: bool = False) -> "Manifest":
        """Load the manifest with the provided file input

        If include_defaults is True, load the manifest over the defaut manifest.
        """
        if include_defaults:
            return cls.load_on_default(manifest_file)

        with manifest_file.open() as file:
            return Manifest(**yaml.safe_load(file))

    @classmethod
    def load_latest_from_clusterdb(cls, include_defaults: bool = False) -> "Manifest":
        """Load the latest manifest from clusterdb

        If include_defaults is True, load the manifest over the defaut manifest.
        values.
        """
        if include_defaults:
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
        m = {
            "juju": {"bootstrap_args": []},
            "charms": {},
            "terraform": {},
        }
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
        PluginManager().add_manifest_section(self)
        self.default_manifest_dict = self.get_default_manifest_as_dict()
        # Add custom validations
        self.validate_terraform_keys(self.default_manifest_dict)

        # Add object variables to store
        self.tf_helpers = {}
        self.snap = Snap()
        self.data_location = self.snap.paths.user_data
        self.client = clusterClient()
        self.tfvar_map = self._get_all_tfvar_map()

    def _get_all_tfvar_map(self) -> dict:
        tfvar_map = copy.deepcopy(MANIFEST_ATTRIBUTES_TFVAR_MAP)
        tfvar_map_plugin = PluginManager().get_all_plugin_manfiest_tfvar_map()
        utils.merge_dict(tfvar_map, tfvar_map_plugin)
        return tfvar_map

    # Terraform helper classes
    def get_tfhelper(self, tfplan: str) -> TerraformHelper:
        if self.tf_helpers.get(tfplan):
            return self.tf_helpers.get(tfplan)

        if not (self.terraform and self.terraform.get(tfplan)):
            raise MissingTerraformInfoException(
                f"Terraform information missing in manifest for {tfplan}"
            )

        tfplan_dir = TERRAFORM_DIR_NAMES.get(tfplan, tfplan)
        src = self.terraform.get(tfplan).source
        dst = self.snap.paths.user_common / "etc" / tfplan_dir
        LOG.debug(f"Updating {dst} from {src}...")
        shutil.copytree(src, dst, dirs_exist_ok=True)

        self.tf_helpers[tfplan] = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / tfplan_dir,
            plan=tfplan,
            backend="http",
            data_location=self.data_location,
        )

        return self.tf_helpers[tfplan]

    def update_tfvars_and_apply_tf(
        self,
        tfplan: str,
        tfvar_config: Optional[str] = None,
        override_tfvars: dict = {},
    ) -> None:
        """Updates terraform vars and Apply the terraform.

        Get tfvars from cluster db using tfvar_config key, Manifest file using
        Charm Manifest tfvar map from core and plugins, User provided override_tfvars.
        Merge the tfvars in the above order so that terraform vars in override_tfvars
        will have highest priority.
        Get tfhelper object for tfplan and write tfvars and apply the terraform plan.

        :param tfplan: Terraform plan to use to get tfhelper
        :type tfplan: str
        :param tfvar_config: TerraformVar key name used to save tfvar in clusterdb
        :type tfvar_config: str or None
        :param override_tfvars: Terraform vars to override
        :type override_tfvars: dict
        """
        tfvars = {}
        if tfvar_config:
            try:
                tfvars_from_config = read_config(self.client, tfvar_config)
                # Exclude all default tfvar keys from the previous terraform
                # vars applied to the plan.
                _tfvar_names = self._get_tfvar_names(tfplan)
                tfvars = {
                    k: v for k, v in tfvars_from_config.items() if k not in _tfvar_names
                }
            except ConfigItemNotFoundException:
                pass

        # NOTE: It is expected for Manifest to contain all previous changes
        # So override tfvars from configdb to defaults if not specified in
        # manifest file
        tfvars.update(self._get_tfvars(tfplan))

        tfvars.update(override_tfvars)
        if tfvar_config:
            update_config(self.client, tfvar_config, tfvars)

        tfhelper = self.get_tfhelper(tfplan)
        LOG.debug(f"Writing tfvars {tfvars}")
        tfhelper.write_tfvars(tfvars)
        tfhelper.apply()

    def _get_tfvars(self, tfplan: str) -> dict:
        """Get tfvars from the manifest.

        MANIFEST_ATTRIBUTES_TFVAR_MAP holds the mapping of Manifest attributes
        and the terraform variable name. For each terraform variable in
        MANIFEST_ATTRIBUTES_TFVAR_MAP, get the corresponding value from Manifest
        and return all terraform variables as dict.
        """
        tfvars = {}

        charms_tfvar_map = self.tfvar_map.get(tfplan, {}).get("charms", {})

        # handle tfvars for charms section
        for charm, per_charm_tfvar_map in charms_tfvar_map.items():
            charm_ = self.charms.get(charm)
            if charm_:
                manifest_charm = asdict(charm_)
                for charm_attribute, tfvar_name in per_charm_tfvar_map.items():
                    charm_attribute_ = manifest_charm.get(charm_attribute)
                    if charm_attribute_:
                        tfvars[tfvar_name] = charm_attribute_

        return tfvars

    def _get_tfvar_names(self, tfplan: str) -> list:
        return [
            tfvar_name
            for charm, per_charm_tfvar_map in self.tfvar_map.get(tfplan, {})
            .get("charms", {})
            .items()
            for charm_attribute, tfvar_name in per_charm_tfvar_map.items()
        ]


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
