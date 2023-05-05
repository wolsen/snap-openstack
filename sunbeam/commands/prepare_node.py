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

import click
from rich.console import Console

console = Console()


JUJU_CHANNEL = "3.2/beta"

PREPARE_NODE_TEMPLATE = f"""#!/bin/bash
# :warning: Node Preparation for OpenStack Sunbeam :warning:
# All of these commands perform privileged operations
# please review carefully before execution.
USER=$(whoami)

# Connect snap to the ssh-keys interface to allow
# read access to private keys - this supports bootstrap
# of the Juju controller to the local machine via SSH.
sudo snap connect openstack:ssh-keys

# Add $USER to the snap_daemon group and adopt new permissions
# supporting interaction with the sunbeam clustering daemon for
# cluster operations.
sudo addgroup $USER snap_daemon
newgrp snap_daemon

# Generate keypair and set-up prompt-less access to local machine
[ -f $HOME/.ssh/id_rsa ] || ssh-keygen -b 4096 -f $HOME/.ssh/id_rsa -t rsa -N ""
cat $HOME/.ssh/id_rsa.pub >> $HOME/.ssh/authorized_keys
ssh-keyscan -H $(hostname --all-ip-addresses) >> $HOME/.ssh/known_hosts

# Install the Juju snap
sudo snap install --channel {JUJU_CHANNEL} juju

# Workaround a bug between snapd and juju
mkdir -p $HOME/.local/share
"""


@click.command()
def prepare_node_script() -> None:
    """Generate script to prepare a node for Sunbeam use."""
    console.print(PREPARE_NODE_TEMPLATE)
