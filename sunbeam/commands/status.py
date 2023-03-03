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

from sunbeam import utils
from sunbeam.clusterd.client import Client as clusterClient
from sunbeam.jobs.common import ResultType

LOG = logging.getLogger(__name__)
console = Console()


def get_cluster_members() -> list:
    try:
        client = clusterClient()
        members = client.cluster.get_cluster_members()
        return members
    except ClusterServiceUnavailableException as e:
        LOG.warning(e)
        return []


@click.command()
@click.option(
    "--wait-ready", default=False, is_flag=True, help="Wait for microstack to be Active"
)
@click.option(
    "--timeout", default=300, type=int, help="Timeout in seconds for microstack status"
)
def status(wait_ready: bool, timeout: int) -> None:
    """Status of the node.

    Print status of the cluster.
    """

    plan = []

    status_overall = []
    for step in plan:
        LOG.debug(f"Starting step {step.name}")
        message = f"{step.description} ... "
        with console.status(f"{step.description} ... "):
            if step.is_skip():
                LOG.debug(f"Skipping step {step.name}")
                continue

            LOG.debug(f"Running step {step.name}")
            result = step.run()
            if result.result_type == ResultType.COMPLETED:
                if isinstance(result.message, list):
                    status_overall.extend(result.message)
                elif isinstance(result.message, str):
                    status_overall.append(result.message)
            LOG.debug(
                f"Finished running step {step.name}. " f"Result: {result.result_type}"
            )

        if result.result_type == ResultType.FAILED:
            console.print(f"{message}[red]failed[/red]")
            raise click.ClickException(result.message)

    console.print("Sunbeam status:")
    console.print(get_cluster_members())
    console.print(f"Current Node: {utils.get_fqdn()}")
    for message in status_overall:
        if "active" in message:
            console.print(f"[green]{message}[/green]")
        else:
            console.print(f"[red]{message}[/red]")

    console.print()
    console.print("User Survey: https://microstack.run/survey")
