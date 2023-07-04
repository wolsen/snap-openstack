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


def get_all_registered_groups(self, cli: click.Group) -> dict:
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
    return groups
