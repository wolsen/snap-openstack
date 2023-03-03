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

from sunbeam.clusterd import service


class ClusterService(service.BaseService):
    """Lists and manages cluster."""

    def bootstrap(self, name: str, address: str) -> None:
        data = {"bootstrap": True, "address": address, "name": name}
        self._post("cluster/control", data=json.dumps(data))

    def add_node(self, name: str, role: str) -> str:
        data = {"name": name}
        result = self._post("/cluster/1.0/tokens", data=json.dumps(data))
        data = {"name": name, "role": role}
        self._post("/1.0/nodes", data=json.dumps(data))
        return result.get("metadata")

    def join_node(self, name: str, address: str, token: str) -> None:
        data = {"join_token": token, "address": address, "name": name}
        self._post("cluster/control", data=json.dumps(data))

    def get_cluster_members(self):
        result = []
        cluster = self._get("/cluster/1.0/cluster")
        members = cluster.get("metadata", {})
        keys = ["name", "address", "status"]
        for member in members:
            result.append({k: v for k, v in member.items() if k in keys})
        return result
