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

from sunbeam.commands.clusterd import (
    ClusterInitStep,
)
from sunbeam.commands.juju import (
    BootstrapJujuStep,
)
from sunbeam.jobs.checks import (
    JujuSnapCheck,
)
from sunbeam.jobs.common import (
    run_plan,
    Role,
)

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
@click.option(
    "--role",
    default="converged",
    type=click.Choice(["control", "compute", "converged"], case_sensitive=False),
    help="Specify whether the node will be a control node, a "
    "compute node, or a converged node (default)",
)
def bootstrap(role: str) -> None:
    """Bootstrap the local node.

    Initialize the sunbeam cluster.
    """
    node_role = Role[role.upper()]
    LOG.debug(f"Bootstrap node: role {role}")

    cloud_type = snap.config.get("juju.cloud.type")
    cloud_name = snap.config.get("juju.cloud.name")

    preflight_checks = []
    if node_role.is_control_node():
        preflight_checks.extend([JujuSnapCheck()])

    for check in preflight_checks:
        LOG.debug(f"Starting pre-flight check {check.name}")
        message = f"{check.description} ... "
        with console.status(f"{check.description} ... "):
            result = check.run()
            if result:
                console.print(f"{message}[green]done[/green]")
            else:
                console.print(f"{message}[red]failed[/red]")
                console.print()
                raise click.ClickException(check.message)

    plan = []
    plan.append(ClusterInitStep(role.upper()))

    if node_role.is_control_node():
        plan.append(BootstrapJujuStep(cloud_name, cloud_type))

    run_plan(plan, console)

    click.echo(f"Node has been bootstrapped as a {role} node")


if __name__ == "__main__":
    bootstrap()
