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
from typing import List, Optional

import click
from rich.console import Console
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.commands.bootstrap_state import SetBootstrapped
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
    JujuLoginStep,
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
    StoreMicrok8sConfigStep,
)
from sunbeam.commands.mysql import ConfigureMySQLStep
from sunbeam.commands.openstack import (
    DeployControlPlaneStep,
    PatchLoadBalancerServicesStep,
)
from sunbeam.commands.sunbeam_machine import (
    AddSunbeamMachineUnitStep,
    DeploySunbeamMachineApplicationStep,
)
from sunbeam.commands.terraform import TerraformInitStep
from sunbeam.jobs.checks import (
    DaemonGroupCheck,
    JujuSnapCheck,
    LocalShareCheck,
    SshKeysConnectedCheck,
    SystemRequirementsCheck,
    VerifyHypervisorHostnameCheck,
)
from sunbeam.jobs.common import (
    Role,
    click_option_topology,
    get_step_message,
    roles_to_str_list,
    run_plan,
    run_preflight_checks,
    validate_roles,
)
from sunbeam.jobs.juju import CONTROLLER, JujuHelper
from sunbeam.jobs.manifest import AddManifestStep, Manifest

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "-m",
    "--manifest",
    help="Manifest file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--role",
    "roles",
    multiple=True,
    default=["control", "compute"],
    type=click.Choice(["control", "compute", "storage"], case_sensitive=False),
    callback=validate_roles,
    help="Specify additional roles, compute or storage, for the "
    "bootstrap node. Defaults to the compute role.",
)
@click_option_topology
@click.option(
    "--database",
    default="auto",
    type=click.Choice(
        [
            "auto",
            "single",
            "multi",
        ],
        case_sensitive=False,
    ),
    help=(
        "Allows definition of the intended cluster configuration: "
        "'auto' for automatic determination, "
        "'single' for a single database, "
        "'multi' for a database per service, "
    ),
)
@click.pass_context
def bootstrap(
    ctx: click.Context,
    roles: List[Role],
    topology: str,
    database: str,
    manifest: Optional[Path] = None,
    accept_defaults: bool = False,
) -> None:
    """Bootstrap the local node.

    Initialize the sunbeam cluster.
    """
    client: Client = ctx.obj

    # Validate manifest file
    manifest_obj = None
    if manifest:
        manifest_obj = Manifest.load(
            client, manifest_file=manifest, include_defaults=True
        )
    else:
        manifest_obj = Manifest.get_default_manifest(client)

    LOG.debug(f"Manifest used for deployment - preseed: {manifest_obj.deployment}")
    LOG.debug(f"Manifest used for deployment - software: {manifest_obj.software}")
    preseed = manifest_obj.deployment

    # Bootstrap node must always have the control role
    if Role.CONTROL not in roles:
        LOG.debug("Enabling control role for bootstrap")
        roles.append(Role.CONTROL)
    is_control_node = any(role.is_control_node() for role in roles)
    is_compute_node = any(role.is_compute_node() for role in roles)
    is_storage_node = any(role.is_storage_node() for role in roles)

    fqdn = utils.get_fqdn()

    roles_str = ",".join(role.name for role in roles)
    pretty_roles = ", ".join(role.name.lower() for role in roles)
    LOG.debug(f"Bootstrap node: roles {roles_str}")

    cloud_type = snap.config.get("juju.cloud.type")
    cloud_name = snap.config.get("juju.cloud.name")
    juju_bootstrap_args = manifest_obj.software.juju.bootstrap_args
    data_location = snap.paths.user_data

    preflight_checks = []
    preflight_checks.append(SystemRequirementsCheck())
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(SshKeysConnectedCheck())
    preflight_checks.append(DaemonGroupCheck())
    preflight_checks.append(LocalShareCheck())
    if is_compute_node:
        hypervisor_hostname = utils.get_hypervisor_hostname()
        preflight_checks.append(
            VerifyHypervisorHostnameCheck(fqdn, hypervisor_hostname)
        )

    run_preflight_checks(preflight_checks, console)

    plan = []
    plan.append(JujuLoginStep(data_location))
    plan.append(ClusterInitStep(client, roles_to_str_list(roles)))
    if manifest:
        plan.append(AddManifestStep(client, manifest))
    plan.append(
        BootstrapJujuStep(
            client,
            cloud_name,
            cloud_type,
            CONTROLLER,
            bootstrap_args=juju_bootstrap_args,
            accept_defaults=accept_defaults,
            deployment_preseed=preseed,
        )
    )
    run_plan(plan, console)

    plan2 = []
    plan2.append(CreateJujuUserStep(fqdn))
    plan2.append(ClusterUpdateJujuControllerStep(client, CONTROLLER))
    plan2_results = run_plan(plan2, console)

    token = get_step_message(plan2_results, CreateJujuUserStep)

    plan3 = []
    plan3.append(ClusterAddJujuUserStep(client, fqdn, token))
    plan3.append(BackupBootstrapUserStep(fqdn, data_location))
    plan3.append(SaveJujuUserLocallyStep(fqdn, data_location))
    run_plan(plan3, console)

    jhelper = JujuHelper(client, data_location)

    plan4 = []
    plan4.append(
        RegisterJujuUserStep(client, fqdn, CONTROLLER, data_location, replace=True)
    )
    # Deploy sunbeam machine charm
    plan4.append(TerraformInitStep(manifest_obj.get_tfhelper("sunbeam-machine-plan")))
    plan4.append(DeploySunbeamMachineApplicationStep(client, manifest_obj, jhelper))
    plan4.append(AddSunbeamMachineUnitStep(client, fqdn, jhelper))
    # Deploy Microk8s application during bootstrap irrespective of node role.
    plan4.append(TerraformInitStep(manifest_obj.get_tfhelper("microk8s-plan")))
    plan4.append(
        DeployMicrok8sApplicationStep(
            client,
            manifest_obj,
            jhelper,
            accept_defaults=accept_defaults,
            deployment_preseed=preseed,
        )
    )
    plan4.append(AddMicrok8sUnitStep(client, fqdn, jhelper))
    plan4.append(StoreMicrok8sConfigStep(client, jhelper))
    plan4.append(AddMicrok8sCloudStep(client, jhelper))
    # Deploy Microceph application during bootstrap irrespective of node role.
    plan4.append(TerraformInitStep(manifest_obj.get_tfhelper("microceph-plan")))
    plan4.append(DeployMicrocephApplicationStep(client, manifest_obj, jhelper))

    if is_storage_node:
        plan4.append(AddMicrocephUnitStep(client, fqdn, jhelper))
        plan4.append(
            ConfigureMicrocephOSDStep(
                client,
                fqdn,
                jhelper,
                accept_defaults=accept_defaults,
                deployment_preseed=preseed,
            )
        )

    if is_control_node:
        plan4.append(TerraformInitStep(manifest_obj.get_tfhelper("openstack-plan")))
        plan4.append(
            DeployControlPlaneStep(client, manifest_obj, jhelper, topology, database)
        )

    run_plan(plan4, console)

    plan5 = []

    if is_control_node:
        plan5.append(ConfigureMySQLStep(jhelper))
        plan5.append(PatchLoadBalancerServicesStep(client))

    # NOTE(jamespage):
    # As with MicroCeph, always deploy the openstack-hypervisor charm
    # and add a unit to the bootstrap node if required.
    plan5.append(TerraformInitStep(manifest_obj.get_tfhelper("hypervisor-plan")))
    plan5.append(DeployHypervisorApplicationStep(client, manifest_obj, jhelper))
    if is_compute_node:
        plan5.append(AddHypervisorUnitStep(client, fqdn, jhelper))

    plan5.append(SetBootstrapped(client))
    run_plan(plan5, console)

    click.echo(f"Node has been bootstrapped with roles: {pretty_roles}")


if __name__ == "__main__":
    bootstrap()
