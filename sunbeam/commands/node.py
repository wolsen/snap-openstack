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

import click
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
from sunbeam.commands.configure import (
    SetLocalHypervisorOptions,
)
from sunbeam.commands.hypervisor import AddHypervisorUnitStep
from sunbeam.commands.juju import AddJujuMachineStep  # RemoveJujuUserStep,
from sunbeam.commands.juju import (
    CreateJujuUserStep,
    RegisterJujuUserStep,
    RemoveJujuMachineStep,
    SaveJujuUserLocallyStep,
)
from sunbeam.commands.microk8s import AddMicrok8sUnitStep, RemoveMicrok8sUnitStep
from sunbeam.jobs.checks import DaemonGroupCheck, JujuSnapCheck, SshKeysConnectedCheck
from sunbeam.jobs.common import (
    ResultType,
    Role,
    get_step_message,
    run_plan,
    run_preflight_checks,
)
from sunbeam.jobs.juju import CONTROLLER, JujuHelper

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
@click.option("--name", type=str, prompt=True, help="Fully qualified node name")
def add_node(name: str) -> None:
    """Generates a token for a new server.

    Register new node to the cluster.
    """
    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    plan1 = [
        ClusterAddNodeStep(name),
        CreateJujuUserStep(name),
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
@click.option("--role", default="converged", type=str, help="Role of the node")
def join(token: str, role: str) -> None:
    """Join node to the cluster.

    Join the node to the cluster.
    """
    # Register juju user with same name as Node fqdn
    name = utils.get_fqdn()
    ip = utils.get_local_ip_by_default_route()

    preflight_checks = []
    preflight_checks.append(JujuSnapCheck())
    preflight_checks.append(SshKeysConnectedCheck())
    preflight_checks.append(DaemonGroupCheck())

    run_preflight_checks(preflight_checks, console)

    controller = CONTROLLER
    data_location = snap.paths.user_data

    jhelper = JujuHelper(data_location)

    plan1 = [
        ClusterJoinNodeStep(token, role.upper()),
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

    if Role[role.upper()].is_control_node():
        plan2.append(AddMicrok8sUnitStep(name, jhelper))

    if Role[role.upper()].is_compute_node():
        plan2.extend(
            [
                AddHypervisorUnitStep(name, jhelper),
                SetLocalHypervisorOptions(name, jhelper),
            ]
        )

    run_plan(plan2, console)

    click.echo(f"Node has joined cluster as a {role} node")


@click.command()
def list() -> None:
    """List nodes in the cluster.

    List all nodes in the cluster.
    """
    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    plan = [ClusterListNodeStep()]
    results = run_plan(plan, console)

    list_node_step_result = results.get("ClusterListNodeStep")

    click.echo("Sunbeam Cluster Node List:")
    click.echo(f"{list_node_step_result.message}")


@click.command()
@click.option("--name", type=str, prompt=True, help="Fully qualified node name")
def remove(name: str) -> None:
    """Remove node from the cluster.

    Remove a node from the cluster.
    If the node does not exist, it removes the node
    from the token records.
    """
    data_location = snap.paths.user_data
    jhelper = JujuHelper(data_location)

    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    plan = [
        RemoveMicrok8sUnitStep(name, jhelper),
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
