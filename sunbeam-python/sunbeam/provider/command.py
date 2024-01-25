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
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam.commands.deployment import (
    DeploymentsConfig,
    deployment_path,
    list_deployments,
    register_deployment_type,
)
from sunbeam.jobs.checks import LocalShareCheck
from sunbeam.jobs.common import (
    CONTEXT_SETTINGS,
    FORMAT_TABLE,
    FORMAT_YAML,
    run_preflight_checks,
)
from sunbeam.provider.base import ProviderBase
from sunbeam.provider.local import LOCAL_TYPE, LocalProvider
from sunbeam.provider.maas import MAAS_TYPE, MaasProvider
from sunbeam.utils import CatchGroup

console = Console()
LOG = logging.getLogger(__name__)


DEFAULT = LOCAL_TYPE


def guess_provider(path: Path) -> str:
    """Guess provider from environment."""
    provider = DEFAULT

    if not path.exists():
        return provider

    deployments = DeploymentsConfig.load(path)

    if deployments.active is None:
        LOG.debug("No active deployment found.")
        return provider

    return deployments.get_active().type


@click.group("deployment", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def deployment(ctx):
    """Manage deployments."""
    pass


@deployment.group("add", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def add(ctx):
    """Add a deployment."""
    pass


@deployment.command()
@click.argument("name", type=str)
def switch(name: str) -> None:
    """Switch deployment."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    path = deployment_path(snap)
    deployments_config = DeploymentsConfig.load(path)
    try:
        deployments_config.switch(name)
        click.echo(f"Deployment switched to {name}.")
    except ValueError as e:
        click.echo(str(e))
        sys.exit(1)


@deployment.command()
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
def list(format: str) -> None:
    """List OpenStack deployments."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    path = deployment_path(snap)
    deployments_config = DeploymentsConfig.load(path)
    deployment_list = list_deployments(deployments_config)
    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Deployment")
        table.add_column("Endpoint")
        table.add_column("Type")
        for deployment in deployment_list["deployments"]:
            style = None
            name = deployment["name"]
            url = deployment["url"]
            type = deployment["type"]
            if name == deployment_list["active"]:
                name = name + "*"
                style = "green"
            table.add_row(name, url, type, style=style)
        console.print(table)
    elif format == FORMAT_YAML:
        console.print(yaml.dump(deployment_list), end="")


@deployment.command()
@click.argument("name", type=str)
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
def show(name: str, format: str):
    """Show deployment detail."""
    preflight_checks = [
        LocalShareCheck(),
    ]
    run_preflight_checks(preflight_checks, console)

    snap = Snap()
    path = deployment_path(snap)
    deployments_config = DeploymentsConfig.load(path)
    try:
        deployment = deployments_config.get_deployment(name)
    except ValueError as e:
        click.echo(str(e))
        sys.exit(1)
    if format == FORMAT_TABLE:
        table = Table(show_header=False)
        for header, value in deployment.dict().items():
            table.add_row(f"[bold]{header.capitalize()}[/bold]", str(value))
        console.print(table)
    elif format == FORMAT_YAML:
        console.print(yaml.dump(deployment), end="")


def register_cli(cli: click.Group, provider: str):
    """Register the CLI for the given provider."""
    cli.add_command(deployment)
    providers: dict[str, ProviderBase] = {
        LOCAL_TYPE: LocalProvider(),
        # TODO(gboutry): hook to register deployment type automatically
        MAAS_TYPE: MaasProvider(),
    }
    for provider_type, provider_obj in providers.items():
        provider_obj.register_add_cli(add)
        deployment_type = provider_obj.deployment_type()
        if deployment_type:
            register_deployment_type(*deployment_type)
        if provider_type == provider:
            provider_obj.register_cli(cli, deployment)
