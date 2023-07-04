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

from packaging.version import Version

from sunbeam.clusterd.client import Client
from sunbeam.plugin.interface import utils

LOG = logging.getLogger(__name__)


class ClickInstantiator:
    """Support invoking click commands on instance methods."""

    def __init__(self, command, klass):
        self.command = command
        self.klass = klass

    def __call__(self, *args, **kwargs):
        return self.command(self.klass(), *args, **kwargs)


class BasePlugin(ABC):
    # Version of plugin interface used by Plugin
    interface_version = Version("0.0.1")

    # Version of plugin
    version = Version("0.0.0")

    def __init__(self, name):
        self.name = name
        self.client = Client()
        self.update_plugin_info({})

    @property
    def plugin_key(self) -> str:
        return f"Plugin-{self.name}"

    @classmethod
    def install_hook() -> None:
        pass

    @classmethod
    def upgrade_hook() -> None:
        pass

    @classmethod
    def configure_hook() -> None:
        pass

    @classmethod
    def pre_refresh_hook() -> None:
        pass

    @classmethod
    def post_refresh_hook() -> None:
        pass

    @classmethod
    def remove_hook() -> None:
        pass

    def get_plugin_info(self) -> dict:
        """Get plugin information from clusterdb."""
        return self.client.cluster.get_config(self.plugin_key)

    def update_plugin_info(self, info: dict) -> None:
        """Update plugin information in clusterdb."""
        info_from_db = self.get_plugin_info()
        info_from_db.update(info)
        info_from_db.update({"version": self.version})
        self.client.cluster.update_config(self.plugin_key, info_from_db)

    def validate_commands(self) -> bool:
        # TODO(hemanth): Validate the dictionary if it follows the format
        # {<group>: [{"name": <command name>, "command": <command function>}]}

        # Validate if command functions mentioned in above dict are defined in subclass
        return True

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
                    "command": "enable_subcmd",
                },
            ],
            "disable": [
                {
                    "name": "subcmd",
                    "command": "disable_subcmd",
                },
            ],
            "init": [
                {
                    "name": "subgroup",
                    "command": "trobuleshoot",
                },
            ],
            "subgroup": [
                {
                    "name": "subcmd",
                    "command": "troubleshoot_subcmd",
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
        raise NotImplementedError

    def register(self, groups_dict: dict):
        """Register plugin groups and commands."""
        LOG.debug(f"Registering plugin {self.name}")
        groups = utils.get_all_registered_groups(cli)

        for group, commands in self.commands().items():
            group_obj = groups.get(group)
            if not group_obj:
                cmd_names = [command.get("name") for command in commands]
                LOG.warning(
                    f"Not able to register command {cmd_names} in group {group}"
                )

            for command in commands:
                cmd = getattr(self, command.get("command"))
                cmd_name = command.get("name")
                cmd.callback = ClickInstantiator(cmd.callback, type(self))
                group_obj.add_command(cmd, cmd_name)
                LOG.debug(f"Command {cmd_name} registered in group {group}")

                # Add newly created click groups to the registered groups so that
                # commands within the plugin can be registered on group.
                # This allows plugin to create new groups and commands in single place.
                if isinstance(cmd, click.Group):
                    groups[cmd_name] = cmd


class EnableDisablePlugin(BasePlugin):
    interface_version = Version("0.0.1")

    def __init__(self, name: str):
        super().__init__(name=name)
        self.update_plugin_info({"enabled": "false"})

    @property
    def enabled(self) -> bool:
        info = self.get_plugin_info(self.plugin_key)
        return info.get("enabled", "false").lower() == "true"

    def pre_enable(self):
        pass

    def post_enable(self):
        pass

    @abstractmethod
    def run_enable_plans(self):
        """Run plans to enable plugin."""

    def enable_plugin(self):
        self.pre_enable()
        self.run_enable_plans()
        self.post_enable()
        self.update_plugin_info({"enabled": "true"})

    def pre_disable(self):
        pass

    def post_disable(self):
        pass

    @abstractmethod
    def run_disable_plans(self):
        """Run plans to disable plugin."""

    def disable_plugin(self):
        self.pre_disable()
        self.run_disable_plans()
        self.post_disable()
        self.update_plugin_info({"enabled": "false"})

    def commands(self) -> dict:
        return {
            "enable": [{"name": self.name, "command": "enable_plugin"}],
            "disable": [{"name": self.name, "command": "disable_plugin"}],
        }
