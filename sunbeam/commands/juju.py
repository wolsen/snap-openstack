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
import re
import subprocess
import tempfile
from typing import Optional

import pexpect
import pwgen
import yaml
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.clusterd.client import Client as clusterClient
from sunbeam.jobs.common import BaseStep, Result, ResultType


LOG = logging.getLogger(__name__)
CONTROLLER_MODEL = "admin/controller"
PEXPECT_TIMEOUT = 60


class JujuStepHelper:
    def _get_juju_binary(self) -> str:
        """Get juju binary path."""
        snap = Snap()
        juju_binary = snap.paths.snap / "juju" / "bin" / "juju"
        return str(juju_binary)

    def _juju_cmd(self, *args):
        """Runs the specified juju command line command

        The command will be run using the json formatter. Invoking functions
        do not need to worry about the format or the juju command that should
        be used.

        For example, to run the juju bootstrap microk8s, this method should
        be invoked as:

          self._juju_cmd('bootstrap', 'microk8s')

        Any results from running with json are returned after being parsed.
        Subprocess execution errors are raised to the calling code.

        :param args: command to run
        :return:
        """
        cmd = [self._get_juju_binary()]
        cmd.extend(args)
        cmd.extend(["--format", "json"])

        LOG.debug(f'Running command {" ".join(cmd)}')
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        LOG.debug(
            f"Command finished. stdout={process.stdout}, " "stderr={process.stderr}"
        )

        return json.loads(process.stdout.strip())

    def check_model_present(self, model_name):
        """Determines if the step should be skipped or not.

        :return: True if the Step should be skipped, False otherwise
        """
        LOG.debug("Retrieving model information from Juju")
        models = asyncio.get_event_loop().run_until_complete(self.jhelper.get_models())
        LOG.debug(f"Juju models: {models}")
        return model_name in models

    def get_clouds(self, cloud_type: str) -> list:
        """Get clouds based on cloud type"""
        clouds = []
        clouds_from_juju_cmd = self._juju_cmd("clouds")
        LOG.debug(f"Available clouds in juju are {clouds_from_juju_cmd.keys()}")

        for name, details in clouds_from_juju_cmd.items():
            if details["type"] == cloud_type:
                clouds.append(name)

        LOG.debug(f"There are {len(clouds)} {cloud_type} clouds available: {clouds}")

        return clouds

    def get_controllers(self, clouds: list) -> list:
        """Get controllers hosted on given clouds"""
        existing_controllers = []

        controllers = self._juju_cmd("controllers")
        LOG.debug(f"Found controllers: {controllers.keys()}")
        LOG.debug(controllers)

        controllers = controllers.get("controllers", {})
        if controllers:
            for name, details in controllers.items():
                if details["cloud"] in clouds:
                    existing_controllers.append(name)

        LOG.debug(
            f"There are {len(existing_controllers)} existing k8s "
            f"controllers running: {existing_controllers}"
        )
        return existing_controllers

    def add_cloud(self, cloud_type: str, cloud_name: str) -> bool:
        """Add cloud of type cloud_type."""
        if cloud_type != "manual":
            return False

        cloud_yaml = {"clouds": {}}
        cloud_yaml["clouds"][cloud_name] = {
            "type": "manual",
            "endpoint": utils.get_local_ip_by_default_route(),
        }

        with tempfile.NamedTemporaryFile() as temp:
            temp.write(yaml.dump(cloud_yaml).encode("utf-8"))
            temp.flush()
            cmd = [
                self._get_juju_binary(),
                "add-cloud",
                cloud_name,
                "--file",
                temp.name,
            ]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

        return True


class BootstrapJujuStep(BaseStep, JujuStepHelper):
    """Bootstraps the Juju controller."""

    def __init__(self, cloud_name: str, cloud_type: str):
        super().__init__("Bootstrap Juju", "Bootstrapping Juju onto cloud")

        self.cloud = cloud_name
        self.cloud_type = cloud_type
        self.controller_name = None
        self.juju_clouds = []

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.juju_clouds = self.get_clouds(self.cloud_type)
            if not self.juju_clouds:
                return Result(ResultType.COMPLETED)

            controllers = self.get_controllers(self.juju_clouds)
            if not controllers:
                return Result(ResultType.COMPLETED)

            # Simply use the first existing kubernetes controller we find.
            # We actually probably need to provide a way for this to be
            # influenced, but for now - we'll use the first controller.
            self.controller_name = controllers[0]
            return Result(ResultType.SKIPPED)
        except subprocess.CalledProcessError as e:
            LOG.exception(
                "Error determining whether to skip the bootstrap "
                "process. Defaulting to not skip."
            )
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            if self.cloud not in self.juju_clouds:
                result = self.add_cloud(self.cloud_type, self.cloud)
                if not result:
                    return Result(ResultType.FAILED, "Not able to create cloud")

            cmd = [self._get_juju_binary(), "bootstrap", self.cloud]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error bootstrapping Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))


class CreateJujuUserStep(BaseStep, JujuStepHelper):
    """Create user in juju and grant superuser access."""

    def __init__(self, name: str):
        super().__init__("Create User", "Create user in juju")
        self.username = name
        self.registration_token_regex = r"juju register (.*?)\n"

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            users = self._juju_cmd("list-users")
            user_names = [user.get("user-name") for user in users]
            if self.username in user_names:
                return Result(ResultType.SKIPPED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error getting users list from juju.")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            cmd = [self._get_juju_binary(), "add-user", self.username]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            re_groups = re.search(
                self.registration_token_regex, process.stdout, re.MULTILINE
            )
            token = re_groups.group(1)
            if not token:
                return Result(ResultType.FAILED, "Not able to parse Registration token")

            # Grant superuser access to user.
            cmd = [self._get_juju_binary(), "grant", self.username, "superuser"]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            # Grant write access to controller model
            # Without this step, the user is not able to view controller model
            cmd = [
                self._get_juju_binary(),
                "grant",
                self.username,
                "write",
                "controller",
            ]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            return Result(ResultType.COMPLETED, message=token)
        except subprocess.CalledProcessError as e:
            LOG.exception(f"Error creating user {self.username} in Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))


class RegisterJujuUserStep(BaseStep, JujuStepHelper):
    """Register user in juju."""

    def __init__(self, name: str, controller: str):
        super().__init__("Register User", "Register juju user using token")
        self.username = name
        self.controller = controller
        self.registration_token = None

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            controllers = self._juju_cmd("controllers")
            LOG.debug(f"Found controllers: {controllers.keys()}")
            controllers = controllers.get("controllers", {})
            if controllers:
                return Result(ResultType.SKIPPED)

            # TODO(hemanth): Update to get token for a given user instead of
            # getting all users information. Need changes in
            # sunbeam-microcluster to expose the API
            client = clusterClient()
            users = client.cluster.list_juju_users()
            users_d = {user.get("username"): user.get("token") for user in users}
            self.registration_token = users_d.get(self.username)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error getting controllers list from juju.")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        if not self.registration_token:
            return Result(ResultType.FAILED, "No registration token in Cluster DB")

        new_password_re = r"Enter a new password"
        confirm_password_re = r"Confirm password"
        controller_name_re = r"Enter a name for this controller"

        # TOCHK: password is saved as a macroon with 24hours shelf life and juju
        # client need to login/logout?
        # Does saving the password in $HOME/.local/share/juju/accounts.yaml
        # avoids login/logout?
        password = pwgen.pwgen(12)

        try:
            child = pexpect.spawn(
                self._get_juju_binary(),
                ["register", self.registration_token],
                PEXPECT_TIMEOUT,
            )
            while True:
                index = child.expect(
                    [
                        new_password_re,
                        confirm_password_re,
                        controller_name_re,
                        pexpect.EOF,
                    ],
                    PEXPECT_TIMEOUT,
                )
                LOG.debug(
                    f"Juju registraton: expect got regex related to index {index}"
                )
                if index == 0 or index == 1:
                    child.sendline(password)
                elif index == 2:
                    child.sendline(self.controller)
                elif index == 3:
                    result = child.before.decode()
                    if "ERROR" in result:
                        str_index = result.find("ERROR")
                        return Result(ResultType.FAILED, result[str_index:])

                    LOG.debug("User registration completed")
                    break
        except pexpect.TIMEOUT as e:
            LOG.exception("Error registering juju user {self.username}")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddJujuMachineStep(BaseStep, JujuStepHelper):
    """Add machine in juju."""

    def __init__(self, ip: str):
        super().__init__("Add machine", "Add machine to juju")

        self.machine_ip = ip

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            machines = self._juju_cmd("machines")
            LOG.debug(f"Found machines: {machines}")
            machines = machines.get("machines", {})

            for machine, details in machines.items():
                if self.machine_ip in details.get("ip-addresses"):
                    LOG.debug("Machine already exists")
                    return Result(ResultType.SKIPPED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error getting machines list from juju.")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        auth_message_re = "Are you sure you want to continue connecting"
        try:
            child = pexpect.spawn(
                self._get_juju_binary(),
                ["add-machine", "-m", CONTROLLER_MODEL, f"ssh:{self.machine_ip}"],
                PEXPECT_TIMEOUT * 3,  # 3 minutes
            )
            while True:
                index = child.expect([auth_message_re, pexpect.EOF], PEXPECT_TIMEOUT)
                LOG.debug(
                    f"Juju add-machine: expect got regex related to index {index}"
                )
                if index == 0:
                    child.sendline("yes")
                elif index == 1:
                    result = child.before.decode()
                    if "ERROR" in result:
                        str_index = result.find("ERROR")
                        return Result(ResultType.FAILED, result[str_index:])

                    LOG.debug("Add machine successful")
                    break
        except pexpect.TIMEOUT as e:
            LOG.exception("Error adding machine {self.machine_ip}")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, "TIMED OUT to add machine")

        return Result(ResultType.COMPLETED)
