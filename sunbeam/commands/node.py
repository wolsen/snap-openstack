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
from typing import List

import click
from prettytable import PrettyTable
from rich.console import Console
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.commands.clusterd import (
    ClusterAddJujuUserStep,
    ClusterAddNodeStep,
    ClusterJoinNodeStep,
    ClusterListNodeStep,
    ClusterRemoveNodeStep,
    ClusterUpdateNodeStep,
)
from sunbeam.commands.configure import SetLocalHypervisorOptions
from sunbeam.commands.hypervisor import (
    AddHypervisorUnitStep,
    DeployHypervisorApplicationStep,
)
from sunbeam.commands.juju import AddJujuMachineStep  # RemoveJujuUserStep,
from sunbeam.commands.juju import (
    CreateJujuUserStep,
    JujuGrantModelAccessStep,
    RegisterJujuUserStep,
    RemoveJujuMachineStep,
    SaveJujuUserLocallyStep,
)
from sunbeam.commands.microceph import (
    AddMicrocephUnitStep,
    ConfigureMicrocephOSDStep,
    RemoveMicrocephUnitStep,
)
from sunbeam.commands.microk8s import AddMicrok8sUnitStep, RemoveMicrok8sUnitStep
from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.commands.terraform import TerraformHelper, TerraformInitStep
from sunbeam.jobs.checks import (
    DaemonGroupCheck,
    JujuSnapCheck,
    LocalShareCheck,
    SshKeysConnectedCheck,
    VerifyFQDNCheck,
)
from sunbeam.jobs.common import (
    ResultType,
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


def remove_trailing_dot(value: str) -> str:
    """Remove trailing dot from the value."""
    return value.rstrip(".")


@click.command()
@click.option(
    "--name",
    type=str,
    prompt=True,
    help="Fully qualified node name",
)
def add(name: str) -> None:
    """Generate a token for a new node to join the cluster."""
    preflight_checks = [DaemonGroupCheck(), VerifyFQDNCheck(name)]
    run_preflight_checks(preflight_checks, console)

    name = remove_trailing_dot(name)
    data_location = snap.paths.user_data
    jhelper = JujuHelper(data_location)

    plan1 = [
        ClusterAddNodeStep(name),
        CreateJujuUserStep(name),
        JujuGrantModelAccessStep(jhelper, name, OPENSTACK_MODEL),
    ]

    plan1_results = run_plan(plan1, console)

    user_token = get_step_message(plan1_results, CreateJujuUserStep)

    plan2 = [ClusterAddJujuUserStep(name, user_token)]
    run_plan(plan2, console)

    add_node_step_result = plan1_results.get("ClusterAddNodeStep")
    if add_node_step_result.result_type == ResultType.COMPLETED:
        click.echo(f"Token for the Node {name}: {add_node_step_result.message}")
    elif add_node_step_result.result_type == ResultType.SKIPPED:
        if add_node_step_result.message:
            click.echo(
                f"Token already generated for Node {name}: "
                f"{add_node_step_result.message}"
            )
        else:
            click.echo("Node already a member of the Sunbeam cluster")


@click.command()
@click.option("--token", type=str, help="Join token")
@click.option(
    "--role",
    multiple=True,
    default=["control", "compute"],
    type=click.Choice(["control", "compute", "storage"], case_sensitive=False),
    callback=validate_roles,
    help="Specify which roles the node will be assigned in the cluster.",
)
def join(token: str, role: List[Role]) -> None:
    """Join a new node to the cluster."""
    node_roles = role

    is_control_node = any(role_.is_control_node() for role_ in node_roles)
    is_compute_node = any(role_.is_compute_node() for role_ in node_roles)
    is_storage_node = any(role_.is_storage_node() for role_ in node_roles)

    # Register juju user with same name as Node fqdn
    name = utils.get_fqdn()
    ip = utils.get_local_ip_by_default_route()

    roles_str = ",".join([role_.name for role_ in role])
    pretty_roles = ", ".join([role_.name.lower() for role_ in role])
    LOG.debug(f"Node joining the cluster with roles: {pretty_roles}")

    preflight_checks = []
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(SshKeysConnectedCheck())
    preflight_checks.append(DaemonGroupCheck())
    preflight_checks.append(LocalShareCheck())

    run_preflight_checks(preflight_checks, console)

    controller = CONTROLLER
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

    tfhelper_hypervisor_deploy = TerraformHelper(
        path=snap.paths.user_common / "etc" / "deploy-openstack-hypervisor",
        plan="hypervisor-plan",
        parallelism=1,
        backend="http",
        data_location=data_location,
    )
    jhelper = JujuHelper(data_location)

    plan1 = [
        ClusterJoinNodeStep(token, roles_str.upper()),
        SaveJujuUserLocallyStep(name, data_location),
        RegisterJujuUserStep(name, controller, data_location),
        AddJujuMachineStep(ip),
    ]
    plan1_results = run_plan(plan1, console)

    machine_id = -1
    machine_id_result = get_step_message(plan1_results, AddJujuMachineStep)
    if machine_id_result is not None:
        machine_id = int(machine_id_result)

    jhelper = JujuHelper(data_location)
    plan2 = []
    plan2.append(ClusterUpdateNodeStep(name, role="", machine_id=machine_id))

    if is_control_node:
        plan2.append(AddMicrok8sUnitStep(name, jhelper))

    if is_storage_node:
        plan2.append(AddMicrocephUnitStep(name, jhelper))
        plan2.append(ConfigureMicrocephOSDStep(name, jhelper))

    if is_compute_node:
        plan2.extend(
            [
                TerraformInitStep(tfhelper_hypervisor_deploy),
                DeployHypervisorApplicationStep(tfhelper_hypervisor_deploy, jhelper),
                AddHypervisorUnitStep(name, jhelper),
                SetLocalHypervisorOptions(name, jhelper, join_mode=True),
            ]
        )

    run_plan(plan2, console)

    click.echo(f"Node joined cluster with roles: {pretty_roles}")


@click.command()
def list() -> None:
    """List nodes in the cluster."""
    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    plan = [ClusterListNodeStep()]
    results = run_plan(plan, console)

    list_node_step_result = results.get("ClusterListNodeStep")
    nodes = list_node_step_result.message

    table = PrettyTable()
    table.field_names = ["Node", "Status", "Control", "Compute", "Storage"]
    table_data = []
    for name, node in nodes.items():
        table_data.append(
            [
                name,
                "up" if node.get("status") == "ONLINE" else "down",
                "x" if "CONTROL" in node.get("role", "") else "",
                "x" if "COMPUTE" in node.get("role", "") else "",
                "x" if "STORAGE" in node.get("role", "") else "",
            ]
        )
    if table_data:
        table.add_rows(table_data)

    click.echo(table)


@click.command()
@click.option("--name", type=str, prompt=True, help="Fully qualified node name")
def remove(name: str) -> None:
    """Remove a node from the cluster."""
    data_location = snap.paths.user_data
    jhelper = JujuHelper(data_location)

    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    plan = [
        RemoveMicrok8sUnitStep(name, jhelper),
        RemoveMicrocephUnitStep(name, jhelper),
        RemoveJujuMachineStep(name),
        # Cannot remove user as the same user name cannot be resued,
        # so commenting the RemoveJujuUserStep
        # RemoveJujuUserStep(name),
        ClusterRemoveNodeStep(name),
    ]
    run_plan(plan, console)

    click.echo(f"Removed node {name} from the cluster")
    # Removing machine does not clean up all deployed juju components. This is
    # deliberate, see https://bugs.launchpad.net/juju/+bug/1851489.
    # Without the workaround mentioned in LP#1851489, it is not possible to
    # reprovision the machine back.
    click.echo(
        f"Run command 'sudo /sbin/remove-juju-services' on node {name} "
        "to reuse the machine."
    )
