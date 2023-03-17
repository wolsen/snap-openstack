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

from sunbeam.commands.clusterd import (
    ClusterAddNodeStep,
    ClusterAddJujuUserStep,
    ClusterJoinNodeStep,
    ClusterListNodeStep,
    ClusterRemoveNodeStep,
)
from sunbeam.commands.juju import (
    CreateJujuUserStep,
)
from sunbeam.jobs.common import ResultType

LOG = logging.getLogger(__name__)
console = Console()


def run_plan(plan: list) -> dict:
    results = {}

    for step in plan:
        LOG.debug(f"Starting step {step.name}")
        message = f"{step.description} ... "
        with console.status(f"{step.description} ... "):
            if step.is_skip():
                LOG.debug(f"Skipping step {step.name}")
                console.print(f"{message}[green]done[/green]")
                continue

            LOG.debug(f"Running step {step.name}")
            result = step.run()
            results[step.__class__.__name__] = result
            LOG.warning(results)
            LOG.debug(
                f"Finished running step {step.name}. " f"Result: {result.result_type}"
            )

        if result.result_type == ResultType.FAILED:
            console.print(f"{message}[red]failed[/red]")
            raise click.ClickException(result.message)

        console.print(f"{message}[green]done[/green]")

    return results


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

    plan1_results = run_plan(plan1)

    user_token = None
    create_juju_user_step_result = plan1_results.get("CreateJujuUserStep")
    if create_juju_user_step_result:
        user_token = create_juju_user_step_result.message

    plan2 = [ClusterAddJujuUserStep(name, user_token)]
    run_plan(plan2)

    add_node_step_result = plan1_results.get("ClusterAddNodeStep")
    if add_node_step_result:
        click.echo(f"Token for the Node {name}: {add_node_step_result.message}")
    else:
        # ClusterAddNodeStep skipped as node already part of cluster
        click.echo("Node already part of the sunbeam cluster")


@click.command()
@click.option("--token", type=str, help="Join token")
@click.option("--role", default="converged", type=str, help="Role of the node")
def join(token: str, role: str) -> None:
    """Join node to the cluster.

    Join the node to the cluster.
    """
    step = ClusterJoinNodeStep(token, role.upper())

    LOG.debug(f"Starting step {step.name}")
    message = f"{step.description} ... "
    if step.is_skip():
        LOG.debug(f"Skipping step {step.name}")
        console.print(f"{message}[green]done[/green]")
        click.echo("Node already part of the sunbeam cluster")
    else:
        LOG.debug(f"Running step {step.name}")
        result = step.run()
        LOG.debug(
            f"Finished running step {step.name}. " f"Result: {result.result_type}"
        )

        if result.result_type == ResultType.FAILED:
            console.print(f"{message}[red]failed[/red]")
            raise click.ClickException(result.message)

        console.print(f"{message}[green]done[/green]")
        click.echo(f"Node has been joined as a {role} node")


@click.command()
def list() -> None:
    """List nodes in the cluster.

    List all nodes in the cluster.
    """
    step = ClusterListNodeStep()

    LOG.debug(f"Starting step {step.name}")
    message = f"{step.description} ... "
    if step.is_skip():
        LOG.debug(f"Skipping step {step.name}")
        console.print(f"{message}[green]done[/green]")
    else:
        LOG.debug(f"Running step {step.name}")
        result = step.run()
        LOG.debug(
            f"Finished running step {step.name}. " f"Result: {result.result_type}"
        )

        if result.result_type == ResultType.FAILED:
            console.print(f"{message}[red]failed[/red]")
            raise click.ClickException(result.message)

        console.print(f"{message}[green]done[/green]")
        click.echo("Sunbeam Cluster Node List:")
        click.echo(f"{result.message}")


@click.command()
@click.option("--name", type=str, prompt=True, help="Fully qualified node name")
def remove(name: str) -> None:
    """Remove node from the cluster.

    Remove a node from the cluster.
    If the node does not exist, it removes the node
    from the token records.
    """
    step = ClusterRemoveNodeStep(name)

    LOG.debug(f"Starting step {step.name}")
    message = f"{step.description} ... "
    if step.is_skip():
        LOG.debug(f"Skipping step {step.name}")
        console.print(f"{message}[green]done[/green]")
        click.echo("Node not part of the sunbeam cluster")
    else:
        LOG.debug(f"Running step {step.name}")
        result = step.run()
        LOG.debug(
            f"Finished running step {step.name}. " f"Result: {result.result_type}"
        )

        if result.result_type == ResultType.FAILED:
            console.print(f"{message}[red]failed[/red]")
            raise click.ClickException(result.message)

        console.print(f"{message}[green]done[/green]")
        click.echo(f"Removed Node {name} from the cluster")
