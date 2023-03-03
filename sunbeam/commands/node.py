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
    ClusterJoinNodeStep,
)
from sunbeam.jobs.common import ResultType

LOG = logging.getLogger(__name__)
console = Console()


@click.command()
@click.option("--name", type=str, prompt=True, help="Fully qualified node name")
@click.option("--role", default="converged", type=str, help="Role of the node")
def add_node(name: str, role: str) -> None:
    """Add node to the cluster.

    Register new node to the cluster.
    """
    step = ClusterAddNodeStep(name, role.upper())

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
        click.echo(f"Token for the Node {name}: {result.message}")

    # TODO(hemanth): Need to get already generated token if add node
    # is run multiple times??


@click.command()
@click.option("--token", type=str, help="Join token")
def join_node(token: str) -> None:
    """Join node to the cluster.

    Join the node to the cluster.
    """
    # TODO(hemanth): Get role of node from cluster DB
    role = "converged"

    step = ClusterJoinNodeStep(token)

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
