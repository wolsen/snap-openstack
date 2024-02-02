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
import os
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ManifestItemNotFoundException,
)
from sunbeam.jobs.checks import DaemonGroupCheck, VerifyBootstrappedCheck
from sunbeam.jobs.common import FORMAT_TABLE, FORMAT_YAML, run_preflight_checks
from sunbeam.jobs.manifest import Manifest
from sunbeam.utils import asdict_with_extra_fields

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


@click.command()
@click.option(
    "-f",
    "--manifest-file",
    help="Output file for manifest, defaults to $HOME/.config/openstack/manifest.yaml",
    type=click.Path(dir_okay=False, path_type=Path),
)
@click.pass_context
def generate(
    ctx: click.Context,
    manifest_file: Path | None = None,
):
    """Generate manifest file.

    Generate manifest file with the deployed configuration.
    If the cluster is not bootstrapped, fallback to default
    configuration.
    """
    client: Client = ctx.obj

    # TODO(hemanth): Add an option schema to print the JsonSchema for the
    # Manifest. This will be easier when moved to pydantic 2.x

    if not manifest_file:
        home = os.environ.get("SNAP_REAL_HOME")
        manifest_file = Path(home) / ".config" / "openstack" / "manifest.yaml"

    LOG.debug(f"Creating {manifest_file} parent directory if it does not exist")
    manifest_file.parent.mkdir(mode=0o775, parents=True, exist_ok=True)

    try:
        preflight_checks = [DaemonGroupCheck(), VerifyBootstrappedCheck(client)]
        run_preflight_checks(preflight_checks, console)
        manifest_obj = Manifest.load_latest_from_clusterdb(
            client, include_defaults=True
        )
    except (click.ClickException, ClusterServiceUnavailableException) as e:
        LOG.debug(e)
        LOG.debug("Fallback to generating manifest with defaults")
        manifest_obj = Manifest.get_default_manifest(client)

    try:
        manifest_dict = asdict_with_extra_fields(manifest_obj)
        LOG.debug(f"Manifest dict with extra fields: {manifest_dict}")
        manifest_yaml = yaml.safe_dump(manifest_dict, sort_keys=False)

        # add comment to each line
        manifest_lines = (f"# {line}" for line in manifest_yaml.split("\n"))
        manifest_yaml_commented = "\n".join(manifest_lines)

        with manifest_file.open("w") as file:
            file.write("# Generated Sunbeam Deployment Manifest\n\n")
            file.write(manifest_yaml_commented)
    except Exception as e:
        LOG.debug(e)
        raise click.ClickException(f"Manifest generation failed: {str(e)}")

    click.echo(f"Generated manifest is at {str(manifest_file)}")
