# Copyright (c) 2022 Canonical Ltd.
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

import datetime
import logging
import tarfile
import tempfile
from pathlib import Path

import click
from rich.console import Console
from snaphelpers import Snap

from sunbeam.commands.juju import WriteCharmLogStep, WriteJujuStatusStep
from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.jobs.checks import DaemonGroupCheck
from sunbeam.jobs.common import run_plan, run_preflight_checks
from sunbeam.jobs.juju import CONTROLLER_MODEL, JujuHelper

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
def inspect() -> None:
    """Inspect the sunbeam installation.

    This script will inspect your installation. It will report any issue
    it finds, and create a tarball of logs and traces which can be
    attached to an issue filed against the sunbeam project.
    """
    preflight_checks = []
    preflight_checks.append(DaemonGroupCheck())
    run_preflight_checks(preflight_checks, console)

    data_location = snap.paths.user_data
    jhelper = JujuHelper(data_location)

    time_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"sunbeam-inspection-report-{time_stamp}.tar.gz"
    dump_file: Path = Path(snap.paths.user_common) / file_name

    plan = []
    with tempfile.TemporaryDirectory() as tmpdirname:
        for model in [CONTROLLER_MODEL.split("/")[-1], OPENSTACK_MODEL]:
            status_file = Path(tmpdirname) / f"juju_status_{model}.out"
            debug_file = Path(tmpdirname) / f"debug_log_{model}.out"
            plan.extend(
                [
                    WriteJujuStatusStep(jhelper, model, status_file),
                    WriteCharmLogStep(jhelper, model, debug_file),
                ]
            )

        run_plan(plan, console)

        with tarfile.open(dump_file, "w:gz") as tar:
            tar.add(tmpdirname, arcname="./")

    console.print(f"[green]Output file written to {dump_file}[/green]")
