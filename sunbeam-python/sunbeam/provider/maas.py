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

import sys

import click
import yaml
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam.commands import resize as resize_cmds
from sunbeam.commands.maas import (
    AddMaasDeployment,
    list_deployments,
    maas_path,
    switch_deployment,
)
from sunbeam.jobs.checks import LocalShareCheck, VerifyClusterdNotBootstrappedCheck
from sunbeam.jobs.common import (
    CONTEXT_SETTINGS,
    FORMAT_TABLE,
    FORMAT_YAML,
    run_plan,
    run_preflight_checks,
)
from sunbeam.provider.base import ProviderBase
from sunbeam.utils import CatchGroup

console = Console()


@click.group("cluster", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def cluster(ctx):
    """Manage the Sunbeam Cluster"""


@click.group("maas", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def maas(ctx):
    """Manage MAAS-backed deployments."""


class MaasProvider(ProviderBase):
    def register_cli(self, cli: click.Group):
        cli.add_command(cluster)
        cluster.add_command(bootstrap)
        cluster.add_command(list)
        cluster.add_command(resize_cmds.resize)
        cli.add_command(maas)
        maas.add_command(add)
        maas.add_command(switch)
        maas.add_command(list_openstack_deployments)


@click.command()
def bootstrap() -> None:
    """Bootstrap the MAAS-backed deployment.

    Initialize the sunbeam cluster.
    """
    raise NotImplementedError


@click.command()
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format.",
)
def list(format: str) -> None:
    """List nodes in the custer."""
    raise NotImplementedError


@click.command()
@click.option("-n", "--name", type=str, prompt=True, help="Name of the deployment")
@click.option("-t", "--token", type=str, prompt=True, help="API token")
@click.option("-u", "--url", type=str, prompt=True, help="API URL")
@click.option("-r", "--resource-pool", type=str, prompt=True, help="Resource pool")
def add(name: str, token: str, url: str, resource_pool: str) -> None:
    """Add MAAS-backed deployment to registered deployments."""
    preflight_checks = [
        LocalShareCheck(),
        VerifyClusterdNotBootstrappedCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    path = maas_path(snap)
    plan = []
    plan.append(AddMaasDeployment(name, token, url, resource_pool, path))
    run_plan(plan, console)
    click.echo(f"MAAS deployment {name} added.")


@click.command()
@click.option("-n", "--name", type=str, prompt=True, help="Name of the deployment")
def switch(name: str) -> None:
    """Switch deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    path = maas_path(snap)
    try:
        switch_deployment(path, name)
        click.echo(f"Deployment switched to {name}.")
    except ValueError as e:
        click.echo(str(e))
        sys.exit(1)


@click.command("list")
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
def list_openstack_deployments(format: str) -> None:
    """List OpenStack deployments."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    path = maas_path(snap)
    deployment_list = list_deployments(path)
    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Deployment")
        table.add_column("MAAS URL")
        table.add_column("Resource Pool")
        for deployment in deployment_list["deployments"]:
            style = None
            name = deployment["name"]
            url = deployment["url"]
            pool = deployment["resource_pool"]
            if name == deployment_list["active"]:
                name = name + "*"
                style = "green"
            table.add_row(name, url, pool, style=style)
        console.print(table)
    elif format == FORMAT_YAML:
        console.print(yaml.dump(deployment_list), end="")
