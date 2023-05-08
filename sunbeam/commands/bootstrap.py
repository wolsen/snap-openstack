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
from pathlib import Path
from typing import List, Optional

import click
from rich.console import Console
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.commands.clusterd import (
    ClusterAddJujuUserStep,
    ClusterInitStep,
    ClusterUpdateJujuControllerStep,
)
from sunbeam.commands.hypervisor import (
    AddHypervisorUnitStep,
    DeployHypervisorApplicationStep,
)
from sunbeam.commands.juju import (
    BackupBootstrapUserStep,
    BootstrapJujuStep,
    CreateJujuUserStep,
    RegisterJujuUserStep,
    SaveJujuUserLocallyStep,
)
from sunbeam.commands.microceph import (
    AddMicrocephUnitStep,
    ConfigureMicrocephOSDStep,
    DeployMicrocephApplicationStep,
)
from sunbeam.commands.microk8s import (
    AddMicrok8sCloudStep,
    AddMicrok8sUnitStep,
    DeployMicrok8sApplicationStep,
)
from sunbeam.commands.openstack import DeployControlPlaneStep
from sunbeam.commands.terraform import TerraformHelper, TerraformInitStep
from sunbeam.jobs.checks import (
    DaemonGroupCheck,
    JujuSnapCheck,
    LocalShareCheck,
    SshKeysConnectedCheck,
)
from sunbeam.jobs.common import (
    Role,
    get_step_message,
    run_plan,
    run_preflight_checks,
    validate_roles,
)
from sunbeam.jobs.juju import CONTROLLER, JujuHelper

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option("-p", "--preseed", help="Preseed file.", type=click.Path())
@click.option(
    "--role",
    multiple=True,
    default=["control", "compute"],
    type=click.Choice(["control", "compute", "storage"], case_sensitive=False),
    callback=validate_roles,
    help="Specify whether the node will be a control node, a "
    "compute node or a storage node. Defaults to all the roles.",
)
def bootstrap(
    role: List[Role], preseed: Optional[Path] = None, accept_defaults: bool = False
) -> None:
    """Bootstrap the local node.

    Initialize the sunbeam cluster.
    """
    node_roles = role

    is_control_node = any(role_.is_control_node() for role_ in node_roles)
    is_compute_node = any(role_.is_compute_node() for role_ in node_roles)
    is_storage_node = any(role_.is_storage_node() for role_ in node_roles)

    fqdn = utils.get_fqdn()

    roles_str = ",".join([role_.name for role_ in role])
    LOG.debug(f"Bootstrap node: roles {roles_str}")

    cloud_type = snap.config.get("juju.cloud.type")
    cloud_name = snap.config.get("juju.cloud.name")

    data_location = snap.paths.user_data

    # NOTE: install to user writable location
    tfplan_dirs = []
    if is_control_node:
        tfplan_dirs.extend(["deploy-microk8s", "deploy-microceph", "deploy-openstack"])
    if is_compute_node:
        tfplan_dirs.extend(["deploy-openstack-hypervisor"])
    for tfplan_dir in tfplan_dirs:
        src = snap.paths.snap / "etc" / tfplan_dir
        dst = snap.paths.user_common / "etc" / tfplan_dir
        LOG.debug(f"Updating {dst} from {src}...")
        shutil.copytree(src, dst, dirs_exist_ok=True)

    preflight_checks = []
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(SshKeysConnectedCheck())
    preflight_checks.append(DaemonGroupCheck())
    preflight_checks.append(LocalShareCheck())

    run_preflight_checks(preflight_checks, console)

    plan = []
    plan.append(ClusterInitStep(roles_str.upper()))
    plan.append(BootstrapJujuStep(cloud_name, cloud_type, CONTROLLER))
    run_plan(plan, console)

    plan2 = []
    plan2.append(CreateJujuUserStep(fqdn))
    plan2.append(ClusterUpdateJujuControllerStep(CONTROLLER))
    plan2_results = run_plan(plan2, console)

    token = get_step_message(plan2_results, CreateJujuUserStep)

    plan3 = []
    plan3.append(ClusterAddJujuUserStep(fqdn, token))
    plan3.append(BackupBootstrapUserStep(fqdn, data_location))
    plan3.append(SaveJujuUserLocallyStep(fqdn, data_location))
    run_plan(plan3, console)

    tfhelper = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-microk8s",
        plan="microk8s-plan",
        parallelism=1,
        backend="http",
        data_location=data_location,
    )
    tfhelper_openstack_deploy = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-openstack",
        plan="openstack-plan",
        parallelism=1,
        backend="http",
        data_location=data_location,
    )
    tfhelper_hypervisor_deploy = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-openstack-hypervisor",
        plan="hypervisor-plan",
        parallelism=1,
        backend="http",
        data_location=data_location,
    )
    tfhelper_microceph_deploy = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-microceph",
        plan="microceph-plan",
        parallelism=1,
        backend="http",
        data_location=data_location,
    )
    jhelper = JujuHelper(data_location)

    plan4 = []
    plan4.append(RegisterJujuUserStep(fqdn, CONTROLLER, data_location, replace=True))
    plan4.append(TerraformInitStep(tfhelper))
    plan4.append(
        DeployMicrok8sApplicationStep(
            tfhelper, jhelper, accept_defaults=accept_defaults, preseed_file=preseed
        )
    )
    plan4.append(AddMicrok8sUnitStep(fqdn, jhelper))
    plan4.append(AddMicrok8sCloudStep(jhelper))
    # Deploy Microceph application during bootstrap irrespective of node role.
    plan4.append(TerraformInitStep(tfhelper_microceph_deploy))
    plan4.append(DeployMicrocephApplicationStep(tfhelper_microceph_deploy, jhelper))

    if is_storage_node:
        plan4.append(AddMicrocephUnitStep(fqdn, jhelper))
        plan4.append(ConfigureMicrocephOSDStep(fqdn, jhelper))

    if is_control_node:
        plan4.append(TerraformInitStep(tfhelper_openstack_deploy))
        plan4.append(DeployControlPlaneStep(tfhelper_openstack_deploy, jhelper))

    run_plan(plan4, console)

    plan5 = []
    if is_compute_node:
        plan5.append(TerraformInitStep(tfhelper_hypervisor_deploy))
        plan5.append(
            DeployHypervisorApplicationStep(tfhelper_hypervisor_deploy, jhelper)
        )
        plan5.append(AddHypervisorUnitStep(fqdn, jhelper))

    run_plan(plan5, console)

    click.echo(f"Node has been bootstrapped as a {roles_str} node")


if __name__ == "__main__":
    bootstrap()
