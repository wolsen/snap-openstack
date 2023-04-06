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


SSH_INIT_TEMPLATE = """# Generate keypair and set-up prompt-less access to local machine
ssh-keygen -b 4096 -f $HOME/.ssh/id_rsa -t rsa -N ""
cat $HOME/.ssh/id_rsa.pub >> $HOME/.ssh/authorized_keys
ssh-keyscan -H $(hostname --all-ip-addresses) >> $HOME/.ssh/known_hosts
"""


@click.command()
def ssh_init() -> None:
    """Generate SSH init script"""
    console.print(SSH_INIT_TEMPLATE)
