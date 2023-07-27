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
from abc import ABC, abstractmethod
from pathlib import Path

import click
from packaging.version import Version
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.jobs.common import read_config, update_config
from sunbeam.plugins.interface import utils

LOG = logging.getLogger(__name__)


class ClickInstantiator:
    """Support invoking click commands on instance methods."""

    def __init__(self, command, klass):
        self.command = command
        self.klass = klass

    def __call__(self, *args, **kwargs):
        return self.command(self.klass(), *args, **kwargs)


class BasePlugin(ABC):
    """Base class for Plugin interface."""

    # Version of plugin interface used by Plugin
    interface_version = Version("0.0.1")

    # Version of plugin
    version = Version("0.0.0")

    def __init__(self, name: str) -> None:
        """Constructor for Base plugin.

        :param name: Name of the plugin
        """
        self.name = name
        self.client = Client()

    @property
    def plugin_key(self) -> str:
        """Key used to store plugin info in cluster database Config table."""
        return f"Plugin-{self.name}"

    def install_hook(self) -> None:
        """Install hook for the plugin.

        snap-openstack install hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def upgrade_hook(self) -> None:
        """Upgrade hook for the plugin.

        snap-openstack upgrade hook handler invokes this function on all the
        plugins. In case of external repo plugins, this hook is invoked
        whenever the plugin repo gets updated. Plugins should override this
        function if required.
        """
        pass

    def configure_hook(self) -> None:
        """Configure hook for the plugin.

        snap-openstack configure hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def pre_refresh_hook(self) -> None:
        """Pre refresh hook for the plugin.

        snap-openstack pre-refresh hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def post_refresh_hook(self) -> None:
        """Post refresh hook for the plugin.

        snap-openstack post-refresh hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def remove_hook(self) -> None:
        """Remove hook for the plugin.

        snap-openstack remove hook handler invokes this function on all the
        plugins. Plugins should override this function if required.
        """
        pass

    def get_plugin_info(self) -> dict:
        """Get plugin information from clusterdb.

        :returns: Dictionay with plugin details like version, and any other information
                  uploded by plugin.
        """
        try:
            return read_config(self.client, self.plugin_key)
        except ConfigItemNotFoundException as e:
            LOG.debug(str(e))
            return {}

    def update_plugin_info(self, info: dict) -> None:
        """Update plugin information in clusterdb.

        Adds version info as well to the info dictionary to update in the cluster db.

        :param info: Plugin specific information as dictionary
        """
        info_from_db = self.get_plugin_info()
        info_from_db.update(info)
        info_from_db.update({"version": str(self.version)})
        update_config(self.client, self.plugin_key, info_from_db)

    def get_terraform_plans_base_path(self) -> Path:
        """Return Terraform plan base location."""
        return Snap().paths.user_common

    def validate_commands(self) -> bool:
        """validate the commands dictionary.

        Validate if the dictionary follows the format
        {<group>: [{"name": <command name>, "command": <command function>}]}

        Validates if the command is of type click.Group or click.Command.

        :returns: True if validation is successful, else False.
        """
        LOG.debug(f"Validating commands: {self.commands}")
        for group, commands in self.commands().items():
            for command in commands:
                cmd_name = command.get("name")
                cmd_func = command.get("command")
                if None in (cmd_name, cmd_func):
                    LOG.warning(
                        f"Plugin {self.name}: Commands dictionary is not in "
                        "required format"
                    )
                    return False

                if not any(
                    [
                        isinstance(cmd_func, click.Group),
                        isinstance(cmd_func, click.Command),
                    ]
                ):
                    LOG.warning(
                        f"Plugin {self.name}: {cmd_func} should be either "
                        "click.Group or click.Command"
                    )
                    return False

        LOG.debug("Validation successful")
        return True

    def is_openstack_control_plane(self) -> bool:
        """Is plugin deploys openstack control plane.

        :returns: True if plugin deploys openstack control plane, else False.
                  Defaults to False.
        """
        return False

    def is_cluster_bootstrapped(self) -> bool:
        """Is sunbeam cluster bootstrapped.

        :returns: True if sunbeam cluster is bootstrapped, else False.
        """
        return self.client.cluster.check_sunbeam_bootstrapped()

    @abstractmethod
    def commands(self) -> dict:
        """Dict of clickgroup along with commands.

        Should be of form
        {<group>: [{"name": <command name>, "command": <command function>}]}

        command can be click.Group or click.Command.

        Example:
        {
            "enable": [
                {
                    "name": "subcmd",
                    "command": self.enable_subcmd,
                },
            ],
            "disable": [
                {
                    "name": "subcmd",
                    "command": self.disable_subcmd,
                },
            ],
            "init": [
                {
                    "name": "subgroup",
                    "command": self.trobuleshoot,
                },
            ],
            "subgroup": [
                {
                    "name": "subcmd",
                    "command": self.troubleshoot_subcmd,
                },
            ],
        }

        Based on above example, expected the subclass to define following functions:

        @click.command()
        def enable_subcmd(self):
            pass

        @click.command()
        def disable_subcmd(self):
            pass

        @click.group()
        def troublshoot(self):
            pass

        @click.command()
        def troubleshoot_subcmd(self):
            pass

        Example of one function that requires options:

        @click.command()
        @click.option(
            "-t",
            "--token",
            help="Ubuntu Pro token to use for subscription attachment",
            prompt=True,
        )
        def enable_subcmd(self, token: str):
            pass

        The user can invoke the above commands like:

        sunbeam enable subcmd
        sunbeam disable subcmd
        sunbeam troubleshoot subcmd
        """

    def register(self, cli: click.Group) -> None:
        """Register plugin groups and commands.

        :param cli: Sunbeam main cli group
        """
        LOG.debug(f"Registering plugin {self.name}")
        if not self.validate_commands():
            LOG.warning(f"Not able to register the plugin {self.name}")
            return

        groups = utils.get_all_registered_groups(cli)
        LOG.debug(f"Registered groups: {groups}")
        for group, commands in self.commands().items():
            group_obj = groups.get(group)
            if not group_obj:
                cmd_names = [command.get("name") for command in commands]
                LOG.warning(
                    f"Plugin {self.name}: Not able to register command "
                    f"{cmd_names} in group {group} as group does not exist"
                )
                continue

            for command in commands:
                cmd = command.get("command")
                cmd_name = command.get("name")
                if cmd_name in group_obj.list_commands({}):
                    if isinstance(cmd, click.Command):
                        LOG.warning(
                            f"Plugin {self.name}: Discarding adding command "
                            f"{cmd_name} as it already exists in group {group}"
                        )
                    else:
                        # Should be sub group and already exists
                        LOG.debug(
                            f"Plugin {self.name}: Group {cmd_name} already "
                            f"part of parent group {group}"
                        )
                    continue

                cmd.callback = ClickInstantiator(cmd.callback, type(self))
                group_obj.add_command(cmd, cmd_name)
                LOG.debug(
                    f"Plugin {self.name}: Command {cmd_name} registered in "
                    f"group {group}"
                )

                # Add newly created click groups to the registered groups so that
                # commands within the plugin can be registered on group.
                # This allows plugin to create new groups and commands in single place.
                if isinstance(cmd, click.Group):
                    groups[cmd_name] = cmd


class EnableDisablePlugin(BasePlugin):
    """Interface for plugins of type on/off.

    Plugins that can be enabled or disabled can use this interface instead
    of BasePlugin.
    """

    interface_version = Version("0.0.1")

    def __init__(self, name: str) -> None:
        """Constructor for plugin interface.

        :param name: Name of the plugin
        """
        super().__init__(name=name)

    @property
    def enabled(self) -> bool:
        """Plugin is enabled or disabled.

        Retrieves enabled field from the Plugin info saved in
        the database and returns enabled based on the enabled field.

        :returns: True if plugin is enabled, else False.
        """
        info = self.get_plugin_info()
        return info.get("enabled", "false").lower() == "true"

    def pre_enable(self) -> None:
        """Handler to perform tasks before enabling the plugin."""
        pass

    def post_enable(self) -> None:
        """Handler to perform tasks after the plugin is enabled."""
        pass

    @abstractmethod
    def run_enable_plans(self) -> None:
        """Run plans to enable plugin.

        The plugin implementation is expected to override this function and
        specify the plans to be run to deploy the workload supported by plugin.
        """

    @abstractmethod
    def enable_plugin(self) -> None:
        """Enable plugin command."""
        self.pre_enable()
        self.run_enable_plans()
        self.post_enable()
        self.update_plugin_info({"enabled": "true"})

    def pre_disable(self) -> None:
        """Handler to perform tasks before disabling the plugin."""
        pass

    def post_disable(self) -> None:
        """Handler to perform tasks after the plugin is disabled."""
        pass

    @abstractmethod
    def run_disable_plans(self) -> None:
        """Run plans to disable plugin.

        The plugin implementation is expected to override this function and
        specify the plans to be run to destroy the workload supported by plugin.
        """

    @abstractmethod
    def disable_plugin(self) -> None:
        """Disable plugin command."""
        self.pre_disable()
        self.run_disable_plans()
        self.post_disable()
        self.update_plugin_info({"enabled": "false"})

    def commands(self) -> dict:
        """Dict of clickgroup along with commands."""
        return {
            "enable": [{"name": self.name, "command": self.enable_plugin}],
            "disable": [{"name": self.name, "command": self.disable_plugin}],
        }
