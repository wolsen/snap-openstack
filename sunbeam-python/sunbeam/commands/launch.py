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

import json
import logging
import os
import subprocess

import click
import openstack
import petname


from rich.console import Console
from snaphelpers import Snap

from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.jobs.juju import (
    JujuHelper,
    ModelNotFoundException,
    run_sync
)

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()

@click.command()
@click.argument(
    "image_name",
    default="ubuntu",
)
@click.option(
    "-k",
    "--key",
    default="sunbeam",
    help="The path to the SSH key to use for the instance"
)
@click.option(
    "-n",
    "--name",
    help="The name for the instance."
)
def launch(
    image_name: str = "ubuntu",
    key: str = "sunbeam",
    name: str = ""
    ) -> None:
    """
    Launch an OpenStack instance
    """
    home = os.environ.get("SNAP_REAL_HOME")
    data_location = snap.paths.user_data
    jhelper = JujuHelper(data_location)
    with console.status("Fetching user credentials ... "):
        try:
            run_sync(jhelper.get_model(OPENSTACK_MODEL))
        except ModelNotFoundException:
            LOG.error(f"Expected model {OPENSTACK_MODEL} missing")
            raise click.ClickException("Please run `sunbeam cluster bootstrap` first")

        app = "keystone"
        action_cmd = "get-admin-account"
        unit = run_sync(jhelper.get_leader_unit(app, model=OPENSTACK_MODEL))
        if not unit:
            _message = f"Unable to get{app} leader"
            raise click.ClickException(_message)

        action_result = run_sync(jhelper.run_action(unit, OPENSTACK_MODEL, action_cmd))
        if action_result.get("return-code", 0) > 1:
            _message = "Unable to retrieve openrc from Keystone service"
            raise click.ClickException(_message)

        try:
            terraform = str(snap.paths.snap / "bin" / "terraform")
            cmd = [terraform, "output", "-json"]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=snap.paths.user_common / "etc" /"demo-setup",
            )
            tf_output = json.loads(process.stdout)

        except subprocess.CalledProcessError:
            LOG.exception("Error initializing Terraform")
            return

    console.print("Launching an OpenStack instance ... ")
    try:
        conn = openstack.connect(
            auth_url=action_result.get("public-endpoint"),
            username=tf_output["OS_USERNAME"]["value"],
            password=tf_output["OS_PASSWORD"]["value"],
            project_name=tf_output["OS_PROJECT_NAME"]["value"],
            user_domain_name=tf_output["OS_USER_DOMAIN_NAME"]["value"],
            project_domain_name=tf_output["OS_PROJECT_DOMAIN_NAME"]["value"]
        )
    except openstack.exceptions.SDKException:
        console.print(
            "Unable to connect to OpenStack.",
            " Is OpenStack running?",
            " Have you run the configure command?",
            " Do you have a clouds.yaml file?",
        )
        return

    with console.status("Checking for SSH key pair ... "):
        key_path = f"{home}/.ssh/{key}"
        console.print("Checking for SSH public key in OpenStack ... ")
        try:
            conn.compute.get_keypair(key)
            console.print(f"Found {key} key in OpenStack!")
        except openstack.exceptions.ResourceNotFound:
            console.print(f"No {key} key found in OpenStack. Creating SSH key at {key_path}")
            key_id = conn.compute.create_keypair(name=key)
            with open(key_path, "w", encoding="utf-8") as key_file:
                key_file.write(key_id.private_key)
                os.chmod(key_path, "0o600")

    with console.status("Creating the OpenStack instance ... "):
        instance_name = name if name else petname.Generate()
        image = conn.compute.find_image(image_name)
        flavor = conn.compute.find_flavor("m1.tiny")
        network = conn.network.find_network("demo-network")
        keypair = conn.compute.find_keypair(key)
        server = conn.compute.create_server(
            name=instance_name,
            image_id=image.id,
            flavor_id=flavor.id,
            networks=[{"uuid": network.id}],
            key_name=keypair.name,
        )

        server = conn.compute.wait_for_server(server)
        server_id = server.id

    with console.status("Allocating IP address to instance ... "):
        external_network = conn.network.find_network("external-network")
        ip_ = conn.network.create_ip(floating_network_id=external_network.id)
        conn.compute.add_floating_ip_to_server(server_id, ip_.floating_ip_address)

    console.print(
        "Access instance with", f"`ssh -i {key_path} ubuntu@{ip_.floating_ip_address}`"
    )
