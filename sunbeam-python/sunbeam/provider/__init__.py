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

import enum
import logging
from pathlib import Path

import click
from rich.console import Console
from snaphelpers import Snap

from sunbeam.jobs.checks import LocalShareCheck
from sunbeam.jobs.common import SHARE_PATH, run_preflight_checks

console = Console()
LOG = logging.getLogger(__name__)

PROVIDER_PATH = SHARE_PATH / "provider"


class Provider(enum.Enum):
    LOCAL = "local"
    MAAS = "maas"


DEFAULT = Provider.LOCAL


def guess_provider(path: Path) -> Provider:
    """Guess provider from environment."""
    provider = DEFAULT

    if not path.exists():
        return provider

    try:
        return Provider(path.read_text().strip())
    except ValueError as e:
        raise ValueError(f"Unknown provider: {provider}") from e
    except Exception as e:
        LOG.warn(f"Unable to read provider, {e}")
        return provider


@click.command()
@click.option(
    "--name",
    type=click.Choice([m.value for m in Provider]),
    default=DEFAULT.value,
    help="Which provider to use.",
)
def set_provider(name: str):
    """Configure provider to deploy OpenStack."""
    preflight_checks = [LocalShareCheck()]
    run_preflight_checks(preflight_checks, console)
    snap = Snap()
    provider = snap.paths.real_home / PROVIDER_PATH
    provider.write_text(name)
    click.echo(f"Provider updated to: {name!r}.")


@click.command()
def get_provider():
    """Show current provider."""
    preflight_checks = [LocalShareCheck()]
    run_preflight_checks(preflight_checks, console)
    snap = Snap()
    provider_path = snap.paths.real_home / PROVIDER_PATH
    provider = guess_provider(provider_path)
    click.echo(f"Current provider: {provider.value!r}")


def register_cli(cli: click.Group, provider: Provider):
    """Register the CLI for the given provider."""
    cli.add_command(get_provider)
    cli.add_command(set_provider)
    match provider:
        case Provider.LOCAL:
            from sunbeam.provider.local import LocalProvider

            provider_obj = LocalProvider()
            provider_obj.register_cli(cli)
        case Provider.MAAS:
            from sunbeam.provider.maas import MaasProvider

            provider_obj = MaasProvider()
            provider_obj.register_cli(cli)
        case _:
            raise ValueError(f"Unsupported provider: {provider!r}")
