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


import importlib
import logging
from pathlib import Path

import click
import yaml

LOG = logging.getLogger()


def get_all_registered_groups(cli: click.Group) -> dict:
    """Get all the groups from cli object."""

    def _get_all_groups(group):
        groups = {}
        for cmd in group.list_commands({}):
            obj = group.get_command({}, cmd)
            if isinstance(obj, click.Group):
                # cli group name is init
                if group.name == "init":
                    groups[cmd] = obj
                else:
                    # TODO(hemanth): Should have all parents in the below key
                    groups[f"{group.name}.{cmd}"] = obj

                groups.update(_get_all_groups(obj))

        return groups

    groups = _get_all_groups(cli)
    groups["init"] = cli
    return groups


def get_plugin_classes(plugin_file: Path) -> list:
    """Return list of plugin classes from plugin yaml."""
    plugins_yaml = {}
    with plugin_file.open() as file:
        plugins_yaml = yaml.safe_load(file)

    plugins = plugins_yaml.get("sunbeam-plugins", {}).get("plugins", {})
    plugin_classes_str = [
        plugin.get("path") for plugin in plugins if plugin.get("path")
    ]

    plugin_classes = []
    for plugin_class in plugin_classes_str:
        module_class_ = plugin_class.rsplit(".", 1)
        try:
            module = importlib.import_module(module_class_[0])
            plugin_class = getattr(module, module_class_[1])
            plugin_classes.append(plugin_class)
        except (ModuleNotFoundError, AttributeError) as e:
            LOG.debug(str(e))
            LOG.warning(f"Ignored loading plugin: {plugin_class}")
            continue

    return plugin_classes
