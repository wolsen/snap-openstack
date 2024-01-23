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


import logging

import click
import yaml
from rich.console import Console
from rich.table import Table

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ManifestItemNotFoundException,
)
from sunbeam.jobs.checks import DaemonGroupCheck
from sunbeam.jobs.common import FORMAT_TABLE, FORMAT_YAML, run_preflight_checks

LOG = logging.getLogger(__name__)
console = Console()


@click.command()
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format.",
)
@click.pass_context
def list(ctx: click.Context, format: str) -> None:
    """List manifests"""
    client: Client = ctx.obj
    manifests = []

    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    try:
        manifests = client.cluster.list_manifests()
    except ClusterServiceUnavailableException:
        click.echo("Error: Not able to connect to Cluster DB")
        return

    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("ID", justify="left")
        table.add_column("Applied Date", justify="left")
        for manifest in manifests:
            table.add_row(manifest.get("manifestid"), manifest.get("applieddate"))
        console.print(table)
    elif format == FORMAT_YAML:
        for manifest in manifests:
            manifest.pop("data")
        click.echo(yaml.dump(manifests))


@click.command()
@click.option("--id", type=str, prompt=True, help="Manifest ID")
@click.pass_context
def show(ctx: click.Context, id: str) -> None:
    """Show Manifest data.

    Use '--id=latest' to get the last committed manifest.
    """
    client: Client = ctx.obj

    preflight_checks = [DaemonGroupCheck()]
    run_preflight_checks(preflight_checks, console)

    try:
        manifest = client.cluster.get_manifest(id)
        click.echo(manifest.get("data"))
    except ClusterServiceUnavailableException:
        click.echo("Error: Not able to connect to Cluster DB")
    except ManifestItemNotFoundException:
        click.echo(f"Error: No manifest exists with id {id}")
