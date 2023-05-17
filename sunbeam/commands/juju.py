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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import pexpect
import pwgen
import yaml
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.clusterd.client import Client as clusterClient
from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
    ControllerNotFoundException,
    JujuAccount,
    JujuAccountNotFound,
    JujuHelper,
    ModelNotFoundException,
    run_sync,
)

LOG = logging.getLogger(__name__)
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

    def check_model_present(self, model_name) -> bool:
        """Determines if the step should be skipped or not.

        :return: True if the Step should be skipped, False otherwise
        """
        try:
            run_sync(self.jhelper.get_model(model_name))
            return True
        except ModelNotFoundException:
            LOG.debug(f"Model {model_name} not found")
            return False

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

    def get_controller(self, controller: str) -> dict:
        """Get controller definition."""
        try:
            return self._juju_cmd("show-controller", controller)[controller]
        except subprocess.CalledProcessError as e:
            LOG.debug(e)
            raise ControllerNotFoundException() from e

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

    def __init__(self, cloud_name: str, cloud_type: str, controller: str):
        super().__init__("Bootstrap Juju", "Bootstrapping Juju onto machine")

        self.cloud = cloud_name
        self.cloud_type = cloud_type
        self.controller = controller
        self.juju_clouds = []

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.get_controller(self.controller)
            return Result(ResultType.SKIPPED)
        except ControllerNotFoundException as e:
            LOG.debug(str(e))
        try:
            self.juju_clouds = self.get_clouds(self.cloud_type)
            return Result(ResultType.COMPLETED)
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

            cmd = [
                self._get_juju_binary(),
                "bootstrap",
                self.cloud,
                self.controller,
            ]
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
        super().__init__("Create User", "Creating user for machine in Juju")
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
                return Result(ResultType.FAILED, "Not able to parse registration token")

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
                CONTROLLER_MODEL,
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


class RemoveJujuUserStep(BaseStep, JujuStepHelper):
    """Remove user in juju."""

    def __init__(self, name: str):
        super().__init__("Remove User", "Removing machine user from Juju")
        self.username = name

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
            if self.username not in user_names:
                return Result(ResultType.SKIPPED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error getting list of users from Juju.")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            cmd = [self._get_juju_binary(), "remove-user", self.username, "--yes"]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception(f"Error removing user {self.username} from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))


class RegisterJujuUserStep(BaseStep, JujuStepHelper):
    """Register user in juju."""

    def __init__(
        self, name: str, controller: str, data_location: Path, replace: bool = False
    ):
        super().__init__("Register Juju User", "Registering Juju user using token")
        self.username = name
        self.controller = controller
        self.data_location = data_location
        self.replace = replace
        self.registration_token = None
        self.juju_account = None

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.juju_account = JujuAccount.load(self.data_location)
            LOG.debug(f"Local account found: {self.juju_account.user}")
        except JujuAccountNotFound as e:
            LOG.warning(e)
            return Result(ResultType.FAILED, "Account was not registered locally")
        try:
            user = self._juju_cmd("show-user")
            LOG.debug(f"Found user: {user['user-name']}")
            username = user["user-name"]
            if username == self.juju_account.user:
                return Result(ResultType.SKIPPED)
        except subprocess.CalledProcessError as e:
            if "No controllers registered" not in e.stderr:
                LOG.exception("Error retrieving authenticated user from Juju.")
                LOG.warning(e.stderr)
                return Result(ResultType.FAILED, str(e))
            # Error is about no controller register, which is okay is this case
            pass

        client = clusterClient()
        user = client.cluster.get_juju_user(self.username)
        self.registration_token = user.get("token")
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        if not self.registration_token:
            return Result(
                ResultType.FAILED, "No registration token found in Cluster database"
            )

        new_password_re = r"Enter a new password"
        confirm_password_re = r"Confirm password"
        controller_name_re = r"Enter a name for this controller"
        expect_list = [
            new_password_re,
            confirm_password_re,
            controller_name_re,
            pexpect.EOF,
        ]

        # TOCHK: password is saved as a macroon with 24hours shelf life and juju
        # client need to login/logout?
        # Does saving the password in $HOME/.local/share/juju/accounts.yaml
        # avoids login/logout?
        register_args = ["register", self.registration_token]
        if self.replace:
            register_args.append("--replace")

        try:
            child = pexpect.spawn(
                self._get_juju_binary(),
                register_args,
                PEXPECT_TIMEOUT,
            )
            while True:
                index = child.expect(expect_list, PEXPECT_TIMEOUT)
                LOG.debug(
                    "Juju registraton: expect got regex related to "
                    f"{expect_list[index]}"
                )
                if index == 0 or index == 1:
                    child.sendline(self.juju_account.password)
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
            LOG.exception(f"Error registering user {self.username} in Juju")
            LOG.warning(e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddJujuMachineStep(BaseStep, JujuStepHelper):
    """Add machine in juju."""

    def __init__(self, ip: str):
        super().__init__("Add machine", "Adding machine to Juju model")

        self.machine_ip = ip

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            machines = self._juju_cmd("machines", "-m", CONTROLLER_MODEL)
            LOG.debug(f"Found machines: {machines}")
            machines = machines.get("machines", {})

            for machine, details in machines.items():
                if self.machine_ip in details.get("ip-addresses"):
                    LOG.debug("Machine already exists")
                    return Result(ResultType.SKIPPED, machine)

        except subprocess.CalledProcessError as e:
            LOG.exception("Error retrieving machines list from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        auth_message_re = "Are you sure you want to continue connecting"
        expect_list = [auth_message_re, pexpect.EOF]
        try:
            child = pexpect.spawn(
                self._get_juju_binary(),
                ["add-machine", "-m", CONTROLLER_MODEL, f"ssh:{self.machine_ip}"],
                PEXPECT_TIMEOUT * 3,  # 3 minutes
            )
            while True:
                index = child.expect(expect_list, PEXPECT_TIMEOUT)
                LOG.debug(
                    "Juju add-machine: expect got regex related to "
                    f"{expect_list[index]}"
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

            # TODO(hemanth): Need to wait until machine comes to started state
            # from planned state?

            machines = self._juju_cmd("machines", "-m", CONTROLLER_MODEL)
            LOG.debug(f"Found machines: {machines}")
            machines = machines.get("machines", {})
            for machine, details in machines.items():
                if self.machine_ip in details.get("ip-addresses"):
                    return Result(ResultType.COMPLETED, machine)

            # respond with machine id as -1 if machine is not reflected in juju
            return Result(ResultType.COMPLETED, "-1")
        except pexpect.TIMEOUT as e:
            LOG.exception("Error adding machine {self.machine_ip} to Juju")
            LOG.warning(e)
            return Result(ResultType.FAILED, "TIMED OUT to add machine")


class RemoveJujuMachineStep(BaseStep, JujuStepHelper):
    """Remove machine in juju."""

    def __init__(self, name: str):
        super().__init__("Remove machine", f"Removing machine {name} from Juju model")

        self.name = name
        self.machine_id = -1

        home = os.environ.get("SNAP_REAL_HOME")
        os.environ["JUJU_DATA"] = f"{home}/.local/share/juju"

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        client = clusterClient()
        try:
            node = client.cluster.get_node_info(self.name)
            self.machine_id = node.get("machineid")
        except NodeNotExistInClusterException as e:
            return Result(ResultType.FAILED, str(e))

        try:
            machines = self._juju_cmd("machines", "-m", CONTROLLER_MODEL)
            LOG.debug(f"Found machines: {machines}")
            machines = machines.get("machines", {})

            if str(self.machine_id) not in machines:
                LOG.debug("Machine does not exist")
                return Result(ResultType.SKIPPED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error retrieving machine list from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            if self.machine_id == -1:
                return Result(
                    ResultType.FAILED,
                    "Not able to retrieve machine id from Cluster database",
                )

            cmd = [
                self._get_juju_binary(),
                "remove-machine",
                "-m",
                CONTROLLER_MODEL,
                str(self.machine_id),
                "--no-prompt",
            ]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception(f"Error removing machine {self.machine_id} from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))


class BackupBootstrapUserStep(BaseStep, JujuStepHelper):
    """Backup bootstrap user credentials"""

    def __init__(self, name: str, data_location: Path):
        super().__init__("Backup Bootstrap User", "Saving bootstrap user credentials")
        self.username = name
        self.data_location = data_location

        home = os.environ.get("SNAP_REAL_HOME")
        self.juju_data = Path(f"{home}/.local/share/juju")

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            user = self._juju_cmd("show-user")
            LOG.debug(f"Found user: {user['user-name']}")
            username = user["user-name"]
            if username == "admin":
                return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error retrieving user from Juju")
            LOG.warning(e.stderr)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        original_accounts = self.juju_data / "accounts.yaml"
        backup_accounts = self.data_location / "accounts.yaml.bk"

        shutil.copy(original_accounts, backup_accounts)
        backup_accounts.chmod(0o660)

        return Result(ResultType.COMPLETED)


class SaveJujuUserLocallyStep(BaseStep):
    """Save user locally."""

    def __init__(self, name: str, data_location: Path):
        super().__init__("Save User", "Save Juju user for local usage")
        self.username = name
        self.data_location = data_location

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            juju_account = JujuAccount.load(self.data_location)
            LOG.debug(f"Local account found: {juju_account.user}")
            # TODO(gboutry): make user password updateable ?
            return Result(ResultType.SKIPPED)
        except JujuAccountNotFound:
            LOG.debug("Local account not found")
            pass

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """

        password = pwgen.pwgen(12)

        juju_account = JujuAccount(
            user=self.username,
            password=password,
        )
        juju_account.write(self.data_location)

        return Result(ResultType.COMPLETED)


class WriteJujuStatusStep(BaseStep, JujuStepHelper):
    """Get the status of the specified model."""

    def __init__(
        self,
        jhelper: JujuHelper,
        model: str,
        file_path: Path,
    ):
        super().__init__("Write Model status", f"Record status of model {model}")

        self.jhelper = jhelper
        self.model = model
        self.file_path = file_path

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            run_sync(self.jhelper.get_model(self.model))
            return Result(ResultType.COMPLETED)
        except ModelNotFoundException:
            LOG.debug(f"Model {self.model} not found")
            return Result(ResultType.SKIPPED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            LOG.debug(f"Getting juju status for model {self.model}")
            _status = run_sync(self.jhelper.get_model_status_full(self.model))
            # Running json.dump directly on the json returned by to_json
            # results in a single line. There is probably a better way of
            # doing this.
            LOG.debug(_status)
            status = json.loads(_status.to_json())

            if not self.file_path.exists():
                self.file_path.touch()
            self.file_path.chmod(0o660)
            with self.file_path.open("w") as file:
                json.dump(status, file, ensure_ascii=False, indent=4)
            return Result(ResultType.COMPLETED, "Inspecting Model Status")
        except Exception as e:  # noqa
            return Result(ResultType.FAILED, str(e))


class WriteCharmLogStep(BaseStep, JujuStepHelper):
    """Get logs for the specified model."""

    def __init__(
        self,
        jhelper: JujuHelper,
        model: str,
        file_path: Path,
    ):
        super().__init__(
            "Get charm logs model", f"Getting charm logs for {model} model"
        )
        self.jhelper = jhelper
        self.model = model
        self.file_path = file_path
        self.model_uuid = None

    def is_skip(self, status: Optional["Status"] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            model = run_sync(self.jhelper.get_model(self.model))
            self.model_uuid = model.info.uuid
            return Result(ResultType.COMPLETED)
        except ModelNotFoundException:
            LOG.debug(f"Model {self.model} not found")
            return Result(ResultType.SKIPPED)

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        LOG.debug(f"Getting debug logs for model {self.model}")
        try:
            # libjuju model.debug_log is broken.
            cmd = [
                self._get_juju_binary(),
                "debug-log",
                "--model",
                self.model_uuid,
                "--replay",
                "--no-tail",
            ]
            # Stream output directly to the file to avoid holding the entire
            # blob of data in RAM.

            if not self.file_path.exists():
                self.file_path.touch()
            self.file_path.chmod(0o660)
            with self.file_path.open("wb") as file:
                subprocess.check_call(cmd, stdout=file)
        except subprocess.CalledProcessError as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED, "Inspecting Charm Log")
