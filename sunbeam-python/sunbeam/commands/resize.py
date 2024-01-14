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
import shutil

import click
from rich.console import Console
from snaphelpers import Snap

from sunbeam.commands.openstack import ResizeControlPlaneStep
from sunbeam.commands.terraform import TerraformHelper, TerraformInitStep
from sunbeam.jobs.common import click_option_topology, run_plan
from sunbeam.jobs.juju import JujuHelper
from sunbeam.jobs.manifest import Manifest
from sunbeam.versions import TERRAFORM_DIR_NAMES

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
@click_option_topology
@click.option(
    "-f", "--force", help="Force resizing to incompatible topology.", is_flag=True
)
def resize(topology: str, force: bool = False) -> None:
    """Expand the control plane to fit available nodes."""

    manifest_obj = Manifest.load_latest_from_clusterdb(on_default=True)

    tfplan = "openstack-plan"
    tfplan_dir = TERRAFORM_DIR_NAMES.get(tfplan)
    manifest_tfplans = manifest_obj.terraform
    src = manifest_tfplans.get(tfplan).source
    dst = snap.paths.user_common / "etc" / tfplan_dir
    LOG.debug(f"Updating {dst} from {src}...")
    shutil.copytree(src, dst, dirs_exist_ok=True)

    data_location = snap.paths.user_data
    tfhelper = TerraformHelper(
        path=snap.paths.user_common / "etc" / tfplan_dir,
        plan=tfplan,
        backend="http",
        data_location=data_location,
    )
    jhelper = JujuHelper(data_location)
    plan = [
        TerraformInitStep(tfhelper),
        ResizeControlPlaneStep(tfhelper, jhelper, topology, force),
    ]

    run_plan(plan, console)

    click.echo("Resize complete.")
