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

from sunbeam.clusterd import service

LOG = logging.getLogger(__name__)


class MicroClusterService(service.BaseService):
    """Client for default MicroCluster Service API."""

    def bootstrap_cluster(self, name: str, address: str) -> None:
        """Bootstrap the micro cluster.

        Boostraps the cluster adding local node specified by
        name as bootstrap node. The address should be in
        format <IP>:<PORT> where the microcluster service
        is running.

        Raises NodeAlreadyExistsException if bootstrap is
        invoked on already existing node in cluster.
        """
        data = {"bootstrap": True, "address": address, "name": name}
        self._post("cluster/control", data=json.dumps(data))

    def join(self, name: str, address: str, token: str) -> None:
        """Join node to the micro cluster.

        Verified the token with the list of saved tokens and
        joins the node with the given name and address.

        Raises NodeAlreadyExistsException if the node is already
        part of the cluster.
        Raises NodeJoinException if the token doesnot match or not
        part of the generated tokens list.
        """
        data = {"join_token": token, "address": address, "name": name}
        self._post("cluster/control", data=json.dumps(data))

    def get_cluster_members(self) -> list:
        """List members in the cluster.

        Returns a list of all members in the cluster.
        """
        result = []
        cluster = self._get("/cluster/1.0/cluster")
        members = cluster.get("metadata", {})
        keys = ["name", "address", "status"]
        for member in members:
            result.append({k: v for k, v in member.items() if k in keys})
        return result

    def remove(self, name: str) -> None:
        """Remove node from the cluster.

        Raises NodeNotExistInClusterException if node does not
        exist in the cluster.
        Raises NodeRemoveFromClusterException if the node is last
        member of the cluster.
        """
        self._delete(f"/cluster/1.0/cluster/{name}")

    def generate_token(self, name: str) -> str:
        """Generate token for the node.

        Generate a new token for the node with name.

        Raises TokenAlreadyGeneratedException if token is already
        generated.
        """
        data = {"name": name}
        result = self._post("/cluster/1.0/tokens", data=json.dumps(data))
        return result.get("metadata")

    def list_tokens(self) -> list:
        """List all generated tokens."""
        tokens = self._get("/cluster/1.0/tokens")
        return tokens.get("metadata")

    def delete_token(self, name: str) -> None:
        """Delete token for the node.

        Raises TokenNotFoundException if token does not exist.
        """
        self._delete(f"/cluster/internal/tokens/{name}")


class ExtendedAPIService(service.BaseService):
    """Client for Sunbeam extended Cluster API."""

    def add_node_info(self, name: str, role: str) -> None:
        """Add Node information to cluster database."""
        data = {"name": name, "role": role}
        self._post("/1.0/nodes", data=json.dumps(data))

    def remove_node_info(self, name: str) -> None:
        """Remove Node information from cluster database."""
        self._delete(f"1.0/nodes/{name}")


class ClusterService(MicroClusterService, ExtendedAPIService):
    """Lists and manages cluster."""

    def bootstrap(self, name: str, address: str, role: str) -> None:
        self.bootstrap_cluster(name, address)
        self.add_node_info(name, role)

    def add_node(self, name: str) -> str:
        return self.generate_token(name)

    def join_node(self, name: str, address: str, token: str, role: str) -> None:
        self.join(name, address, token)
        self.add_node_info(name, role)

    def remove_node(self, name) -> None:
        members = self.get_cluster_members()
        member_names = [member.get("name") for member in members]

        # If node is part of cluster, remove node from cluster
        if name in member_names:
            self.remove_node_info(name)
            self.remove(name)
        else:
            # Check if token exists in token list and remove
            self.delete_token(name)
