# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import Mock, patch

import click
import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.plugins.interface.v1.base import BasePlugin


@pytest.fixture()
def cclient():
    with patch("sunbeam.plugins.interface.v1.base.Client") as p:
        yield p


@pytest.fixture()
def utils():
    with patch("sunbeam.plugins.interface.v1.base.utils") as p:
        yield p


@pytest.fixture()
def clickinstantiator():
    with patch("sunbeam.plugins.interface.v1.base.ClickInstantiator") as p:
        yield p


@pytest.fixture()
def read_config():
    with patch("sunbeam.plugins.interface.v1.base.read_config") as p:
        yield p


@pytest.fixture()
def update_config():
    with patch("sunbeam.plugins.interface.v1.base.update_config") as p:
        yield p


class TestBasePlugin:
    def test_validate_commands(self, cclient):
        BasePlugin.__abstractmethods__ = set()
        with patch.object(BasePlugin, "commands") as mock_commands:
            plugin = BasePlugin(name="test")
            mock_commands.return_value = {
                "group1": [{"name": "cmd1", "command": click.Command("cmd1")}]
            }
            plugin = BasePlugin(name="test")
            result = plugin.validate_commands()
            assert result is True

    def test_validate_commands_missing_command_function(self, cclient):
        BasePlugin.__abstractmethods__ = set()
        with patch.object(BasePlugin, "commands") as mock_commands:
            mock_commands.return_value = {"group1": [{"name": "cmd1"}]}
            plugin = BasePlugin(name="test")
            result = plugin.validate_commands()
            assert result is False

    def test_validate_commands_missing_command_name(self, cclient):
        BasePlugin.__abstractmethods__ = set()
        with patch.object(BasePlugin, "commands") as mock_commands:
            mock_commands.return_value = {
                "group1": [{"command": click.Command("cmd1")}]
            }
            plugin = BasePlugin(name="test")
            result = plugin.validate_commands()
            assert result is False

    def test_validate_commands_empty_command_list(self, cclient):
        BasePlugin.__abstractmethods__ = set()
        with patch.object(BasePlugin, "commands") as mock_commands:
            mock_commands.return_value = {"group1": []}
            plugin = BasePlugin(name="test")
            result = plugin.validate_commands()
            assert result is True

    def test_validate_commands_subgroup_as_command(self, cclient):
        BasePlugin.__abstractmethods__ = set()
        with patch.object(BasePlugin, "commands") as mock_commands:
            mock_commands.return_value = {
                "group1": [{"name": "subgroup1", "command": click.Group("subgroup1")}]
            }
            plugin = BasePlugin(name="test")
            result = plugin.validate_commands()
            assert result is True

    def test_register(self, cclient, utils, clickinstantiator):
        with patch.object(BasePlugin, "commands") as mock_commands:
            cli = Mock()
            mock_groups = Mock()
            mock_group_obj = Mock()
            utils.get_all_registered_groups.return_value = mock_groups
            mock_groups.get.return_value = mock_group_obj
            mock_group_obj.list_commands.return_value = []

            cmd1_obj = click.Command("cmd1")
            mock_commands.return_value = {
                "group1": [{"name": "cmd1", "command": cmd1_obj}]
            }
            plugin = BasePlugin(name="test")
            plugin.register(cli)
            clickinstantiator.assert_called_once()
            mock_group_obj.add_command.assert_called_once_with(cmd1_obj, "cmd1")

    def test_register_when_command_already_exists(
        self, cclient, utils, clickinstantiator
    ):
        with patch.object(BasePlugin, "commands") as mock_commands:
            cli = Mock()
            mock_groups = Mock()
            mock_group_obj = Mock()
            utils.get_all_registered_groups.return_value = mock_groups
            mock_groups.get.return_value = mock_group_obj
            mock_group_obj.list_commands.return_value = ["cmd1"]

            cmd1_obj = click.Command("cmd1")
            mock_commands.return_value = {
                "group1": [{"name": "cmd1", "command": cmd1_obj}]
            }
            plugin = BasePlugin(name="test")
            plugin.register(cli)
            mock_group_obj.add_command.assert_not_called()
            clickinstantiator.assert_not_called()

    def test_register_when_group_doesnot_exists(
        self, cclient, utils, clickinstantiator
    ):
        with patch.object(BasePlugin, "commands") as mock_commands:
            cli = Mock()
            mock_groups = Mock()
            utils.get_all_registered_groups.return_value = mock_groups
            mock_groups.get.return_value = None

            cmd1_obj = click.Command("cmd1")
            mock_commands.return_value = {
                "group1": [{"name": "cmd1", "command": cmd1_obj}]
            }
            plugin = BasePlugin(name="test")
            plugin.register(cli)
            clickinstantiator.assert_not_called()

    def test_get_plugin_info(self, cclient, read_config):
        mock_info = {"version": "0.0.1"}
        read_config.return_value = mock_info
        plugin = BasePlugin(name="test")
        info = plugin.get_plugin_info()
        assert info == mock_info

    def test_get_plugin_info_no_config_in_db(self, cclient, read_config):
        read_config.side_effect = ConfigItemNotFoundException()
        plugin = BasePlugin(name="test")
        info = plugin.get_plugin_info()
        assert info == {}

    def test_update_plugin_info(self, cclient, read_config, update_config):
        mock_info = {}
        read_config.return_value = mock_info
        plugin = BasePlugin(name="test")
        plugin.update_plugin_info({"test": "test"})
        assert update_config.call_args.args[2] == {"test": "test", "version": "0.0.0"}

    def test_update_plugin_info_with_config_in_database(
        self, cclient, read_config, update_config
    ):
        mock_info = {"version": "0.0.1", "enabled": "true"}
        read_config.return_value = mock_info
        plugin = BasePlugin(name="test")
        plugin.update_plugin_info({"test": "test"})
        assert update_config.call_args.args[2] == {
            "enabled": "true",
            "test": "test",
            "version": "0.0.0",
        }
