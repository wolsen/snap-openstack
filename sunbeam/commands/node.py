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
from sunbeam.commands.juju import (
    AddJujuMachineStep,
    CreateJujuUserStep,
    RegisterJujuUserStep,
    RemoveJujuMachineStep,
    # RemoveJujuUserStep,
)
from sunbeam.jobs.common import (
    run_plan,
    ResultType,
)

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
@click.option("--name", type=str, prompt=True, help="Fully qualified node name")
def add_node(name: str) -> None:
    """Generates a token for a new server.

    Register new node to the cluster.
    """
    plan1 = [
        ClusterAddNodeStep(name),
        CreateJujuUserStep(name),
    ]

    plan1_results = run_plan(plan1, console)

    user_token = None
    create_juju_user_step_result = plan1_results.get("CreateJujuUserStep")
    if create_juju_user_step_result:
        user_token = create_juju_user_step_result.message

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
            click.echo("Node already part of the sunbeam cluster")


@click.command()
@click.option("--token", type=str, help="Join token")
@click.option("--role", default="converged", type=str, help="Role of the node")
def join(token: str, role: str) -> None:
    """Join node to the cluster.

    Join the node to the cluster.
    """
    # Resgister juju user with same name as Node fqdn
    name = utils.get_fqdn()
    ip = utils.get_local_ip_by_default_route()

    cloud_name = snap.config.get("juju.cloud.name")
    controller_name = f"{cloud_name}-default"

    plan1 = [
        ClusterJoinNodeStep(token, role.upper()),
        RegisterJujuUserStep(name, controller_name),
        AddJujuMachineStep(ip),
    ]
    plan1_results = run_plan(plan1, console)

    machine_id = -1
    add_juju_machine_step_result = plan1_results.get("AddJujuMachineStep")
    if add_juju_machine_step_result.result_type != ResultType.FAILED:
        machine_id = int(add_juju_machine_step_result.message)

    plan2 = [ClusterUpdateNodeStep(name, role="", machine_id=machine_id)]
    run_plan(plan2, console)

    click.echo(f"Node has been joined as a {role} node")


@click.command()
def list() -> None:
    """List nodes in the cluster.

    List all nodes in the cluster.
    """
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
    plan = [
        RemoveJujuMachineStep(name),
        # Cannot remove user as the same user name cannot be resued,
        # so commenting the RemoveJujuUserStep
        # RemoveJujuUserStep(name),
        ClusterRemoveNodeStep(name),
    ]
    run_plan(plan, console)

    click.echo(f"Removed Node {name} from the cluster")
    # Removing machine does not clean up all deployed juju components. This is
    # deliberate, see https://bugs.launchpad.net/juju/+bug/1851489.
    # Without the workaround mentioned in LP#1851489, it is not possible to
    # reprovision the machine back.
    click.echo(
        f"Run command 'sudo /sbin/remove-juju-services' on node {name} "
        "to reuse the machine."
    )
