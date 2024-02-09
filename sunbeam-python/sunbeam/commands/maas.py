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

"""MAAS management."""

import ast
import builtins
import collections
import copy
import enum
import logging
import ssl
import textwrap
from typing import Type, TypeGuard, overload

import pydantic
import yaml
from maas.client import bones, connect
from rich.console import Console
from rich.status import Status

import sunbeam.commands.microceph as microceph
import sunbeam.commands.microk8s as microk8s
from sunbeam.clusterd.client import Client
from sunbeam.commands.clusterd import APPLICATION as CLUSTERD_APPLICATION
from sunbeam.commands.deployment import Deployment, DeploymentsConfig
from sunbeam.commands.juju import (
    INFRASTRUCTURE_MODEL,
    BootstrapJujuStep,
    ControllerNotFoundException,
    JujuStepHelper,
    ScaleJujuStep,
)
from sunbeam.jobs.checks import Check, DiagnosticsCheck, DiagnosticsResult
from sunbeam.jobs.common import (
    RAM_4_GB_IN_MB,
    RAM_32_GB_IN_MB,
    BaseStep,
    Result,
    ResultType,
)
from sunbeam.jobs.juju import (
    ActionFailedException,
    JujuController,
    JujuHelper,
    LeaderNotFoundException,
    TimeoutException,
    UnitNotFoundException,
    run_sync,
)
from sunbeam.jobs.manifest import Manifest

LOG = logging.getLogger(__name__)
console = Console()

MAAS_CONFIG = "maas.yaml"

MAAS_PUBLIC_IP_RANGE = "sunbeam-public-api"
MAAS_INTERNAL_IP_RANGE = "sunbeam-internal-api"


class Networks(enum.Enum):
    PUBLIC = "public"
    STORAGE = "storage"
    STORAGE_CLUSTER = "storage-cluster"
    INTERNAL = "internal"
    DATA = "data"
    MANAGEMENT = "management"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class MaasDeployment(Deployment):
    type: str = "maas"
    token: str
    resource_pool: str
    network_mapping: dict[str, str | None] = {}
    clusterd_address: str | None = None

    @property
    def controller(self) -> str:
        """Return controller name."""
        return self.name + "-controller"

    @pydantic.validator("type")
    def type_validator(cls, v: str, values: dict) -> str:
        if v != "maas":
            raise ValueError("Deployment type must be MAAS.")
        return v

    @classmethod
    def import_step(cls) -> Type["AddMaasDeployment"]:
        """Return a step for importing a deployment.

        This step will be used to make sure the deployment is valid.
        The step must take as constructor arguments: DeploymentsConfig, Deployment.
        The Deployment must be of the type that the step is registered for.
        """
        return AddMaasDeployment


def is_maas_deployment(deployment: Deployment) -> TypeGuard[MaasDeployment]:
    """Check if deployment is a MAAS deployment."""
    return isinstance(deployment, MaasDeployment)


class RoleTags(enum.Enum):
    CONTROL = "control"
    COMPUTE = "compute"
    STORAGE = "storage"
    JUJU_CONTROLLER = "juju-controller"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


ROLE_NETWORK_MAPPING = {
    RoleTags.CONTROL: [
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.PUBLIC,
        Networks.STORAGE,
    ],
    RoleTags.COMPUTE: [
        Networks.DATA,
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.STORAGE,
    ],
    RoleTags.STORAGE: [
        Networks.DATA,
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.STORAGE,
        Networks.STORAGE_CLUSTER,
    ],
    RoleTags.JUJU_CONTROLLER: [
        Networks.MANAGEMENT,
        # TODO(gboutry): missing public access network to reach charmhub...
    ],
}


class StorageTags(enum.Enum):
    CEPH = "ceph"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class MaasClient:
    """Facade to MAAS APIs."""

    def __init__(self, url: str, token: str, resource_pool: str | None = None):
        self._client = connect(url, apikey=token)
        self.resource_pool = resource_pool

    def get_resource_pool(self, name: str) -> object:
        """Fetch resource pool from MAAS."""
        return self._client.resource_pools.get(name)  # type: ignore

    def list_machines(self, **kwargs) -> list[dict]:
        """List machines."""
        if self.resource_pool:
            kwargs["pool"] = self.resource_pool
        try:
            return self._client.machines.list.__self__._handler.read(**kwargs)  # type: ignore # noqa
        except bones.CallError as e:
            if "No such pool" in str(e):
                raise ValueError(f"Resource pool {self.resource_pool!r} not found.")
            raise e

    def get_machine(self, machine: str) -> dict:
        """Get machine."""
        kwargs = {
            "hostname": machine,
        }
        if self.resource_pool:
            kwargs["pool"] = self.resource_pool
        machines = self._client.machines.list.__self__._handler.read(**kwargs)  # type: ignore # noqa
        if len(machines) == 0:
            raise ValueError(f"Machine {machine!r} not found.")
        if len(machines) > 1:
            raise ValueError(f"Machine {machine!r} not unique.")
        return machines[0]

    def list_spaces(self) -> list[dict]:
        """List spaces."""
        return self._client.spaces.list.__self__._handler.read()  # type: ignore

    def get_space(self, space: str) -> dict:
        """Get a specific space."""
        for space_raw in self.list_spaces():
            if space_raw["name"] == space:
                return space_raw
        else:
            raise ValueError(f"Space {space!r} not found.")

    def get_subnets(self, space: str | None = None) -> list[dict]:
        """List subnets."""
        if space:
            # check if space exists
            _ = self.get_space(space)
        subnets_response: list = self._client.subnets.list()  # type: ignore
        subnets = []
        for subnet in subnets_response:
            if space is None or subnet.space == space:
                subnets.append(subnet._data)
        return subnets

    def get_ip_ranges(self, subnet: dict) -> list[dict]:
        """List ip ranges.

        Only list reserved types as it is the only one we are interested in.
        """
        ip_ranges_response: list = self._client.ip_ranges.list()  # type: ignore

        subnet_id = subnet["id"]
        ip_ranges = []
        for ip_range in ip_ranges_response:
            if ip_range.subnet.id == subnet_id and ip_range.type.value == "reserved":
                ip_ranges.append(ip_range._data)
        return ip_ranges

    @classmethod
    def from_deployment(cls, deployment: Deployment) -> "MaasClient":
        """Return client connected to active deployment."""
        if not is_maas_deployment(deployment):
            raise ValueError("Deployment is not a MAAS deployment.")
        return cls(
            deployment.url,
            deployment.token,
            deployment.resource_pool,
        )


def _convert_raw_machine(machine_raw: dict) -> dict:
    storage_tags = StorageTags.values()
    storage_devices = {tag: [] for tag in storage_tags}
    for blockdevice in machine_raw["blockdevice_set"]:
        for tag in blockdevice["tags"]:
            if tag in storage_tags:
                storage_devices[tag].append(
                    {
                        "name": blockdevice["name"],
                        "id_path": blockdevice["id_path"],
                    }
                )

    spaces = []
    for interface in machine_raw["interface_set"]:
        if (vlan := interface.get("vlan")) and (space := vlan.get("space")):
            spaces.append(space)
    return {
        "system_id": machine_raw["system_id"],
        "hostname": machine_raw["hostname"],
        "roles": list(set(machine_raw["tag_names"]).intersection(RoleTags.values())),
        "zone": machine_raw["zone"]["name"],
        "status": machine_raw["status_name"],
        "storage": storage_devices,
        "spaces": list(set(spaces)),
        "cores": machine_raw["cpu_count"],
        "memory": machine_raw["memory"],
    }


def list_machines(client: MaasClient, **extra_args) -> list[dict]:
    """List machines in deployment, return consumable list of dicts."""
    machines_raw = client.list_machines(**extra_args)

    machines = []
    for machine in machines_raw:
        machines.append(_convert_raw_machine(machine))
    return machines


def get_machine(client: MaasClient, machine: str) -> dict:
    """Get machine in deployment, return consumable dict."""
    machine_raw = client.get_machine(machine)
    machine_dict = _convert_raw_machine(machine_raw)
    LOG.debug("Retrieved machine %s: %r", machine, machine_dict)
    return machine_dict


def _group_machines_by_zone(machines: list[dict]) -> dict[str, list[dict]]:
    """Helper to list machines by zone, return consumable dict."""
    result = collections.defaultdict(list)
    for machine in machines:
        result[machine["zone"]].append(machine)
    return dict(result)


def list_machines_by_zone(client: MaasClient) -> dict[str, list[dict]]:
    """List machines by zone, return consumable dict."""
    machines_raw = list_machines(client)
    return _group_machines_by_zone(machines_raw)


def list_spaces(client: MaasClient) -> list[dict]:
    """List spaces in deployment, return consumable list of dicts."""
    spaces_raw = client.list_spaces()
    spaces = []
    for space_raw in spaces_raw:
        space = {
            "name": space_raw["name"],
            "subnets": [subnet_raw["cidr"] for subnet_raw in space_raw["subnets"]],
        }
        spaces.append(space)
    return spaces


def map_space(
    deployments_config: DeploymentsConfig,
    client: MaasClient,
    space: str,
    network: Networks,
):
    """Map space to network."""
    deployment = deployments_config.get_active()
    if not is_maas_deployment(deployment):
        raise ValueError("Active deployment is not a MAAS deployment.")
    space_raw = client.get_space(space)
    deployment.network_mapping[network.value] = space_raw["name"]
    deployments_config.write()


def unmap_space(deployments_config: DeploymentsConfig, network: Networks):
    """Unmap network."""
    deployment = deployments_config.get_active()
    if not is_maas_deployment(deployment):
        raise ValueError("Active deployment is not a MAAS deployment.")
    deployment.network_mapping.pop(network.value, None)
    deployments_config.write()


@overload
def get_network_mapping(deployment: MaasDeployment) -> dict[str, str | None]:
    pass


@overload
def get_network_mapping(deployment: DeploymentsConfig) -> dict[str, str | None]:
    pass


def get_network_mapping(
    deployment: MaasDeployment | DeploymentsConfig,
) -> dict[str, str | None]:
    """Return network mapping."""
    if isinstance(deployment, DeploymentsConfig):
        dep = deployment.get_active()
    else:
        dep = deployment
    if not is_maas_deployment(dep):
        raise ValueError(f"Deployment {dep.name} is not a MAAS deployment.")
    mapping = dep.network_mapping.copy()
    for network in Networks:
        mapping.setdefault(network.value, None)
    return mapping


def _convert_raw_ip_range(ip_range_raw: dict) -> dict:
    """Convert raw ip range to consumable dict."""
    return {
        "label": ip_range_raw["comment"],
        "start": ip_range_raw["start_ip"],
        "end": ip_range_raw["end_ip"],
    }


def get_ip_ranges_from_space(client: MaasClient, space: str) -> dict[str, list[dict]]:
    """Return all IP ranges from a space.

    Return a dict with the CIDR as key and a list of IP ranges as value.
    """
    subnets = client.get_subnets(space)
    ip_ranges = {}
    for subnet in subnets:
        ranges_raw = client.get_ip_ranges(subnet)
        ranges = []
        for ip_range in ranges_raw:
            ranges.append(_convert_raw_ip_range(ip_range))
        if len(ranges) > 0:
            ip_ranges[subnet["cidr"]] = ranges
    return ip_ranges


ROLES_NEEDED_ERROR = f"""A machine needs roles to be a part of an openstack deployment.
Available roles are: {RoleTags.values()}.
Roles can be assigned to a machine by applying tags in MAAS.
More on assigning tags: https://maas.io/docs/using-machine-tags
"""


class AddMaasDeployment(BaseStep):
    def __init__(
        self,
        deployments_config: DeploymentsConfig,
        deployment: MaasDeployment,
    ) -> None:
        super().__init__(
            "Add MAAS-backed deployment",
            "Adding MAAS-backed deployment for OpenStack usage",
        )
        self.deployments_config = deployments_config
        self.deployment = deployment

    def is_skip(self, status: Status | None = None) -> Result:
        """Check if deployment is already added."""

        try:
            self.deployments_config.get_deployment(self.deployment.name)
            return Result(
                ResultType.FAILED, f"Deployment {self.deployment.name} already exists."
            )
        except ValueError:
            pass

        current_deployments = set()
        for deployment in self.deployments_config.deployments:
            if is_maas_deployment(deployment):
                current_deployments.add(
                    (
                        deployment.url,
                        deployment.resource_pool,
                    )
                )

        if (self.deployment.url, self.deployment.resource_pool) in current_deployments:
            return Result(
                ResultType.FAILED,
                "Deployment with same url and resource pool already exists.",
            )

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Check MAAS is working, Resource Pool exists, write to local configuration."""
        try:
            client = MaasClient(self.deployment.url, self.deployment.token)
            _ = client.get_resource_pool(self.deployment.resource_pool)
        except ValueError as e:
            LOG.debug("Failed to connect to maas", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except bones.CallError as e:
            if e.status == 401:
                LOG.debug("Unauthorized", exc_info=True)
                return Result(
                    ResultType.FAILED,
                    "Unauthorized, check your api token has necessary permissions.",
                )
            elif e.status == 404:
                LOG.debug("Resource pool not found", exc_info=True)
                return Result(
                    ResultType.FAILED,
                    f"Resource pool {self.deployment.resource_pool!r} not"
                    " found in given MAAS URL.",
                )
            LOG.debug("Unknown error", exc_info=True)
            return Result(ResultType.FAILED, f"Unknown error, {e}")
        except Exception as e:
            match type(e.__cause__):
                case builtins.ConnectionRefusedError:
                    LOG.debug("Connection refused", exc_info=True)
                    return Result(
                        ResultType.FAILED, "Connection refused, is the url correct?"
                    )
                case ssl.SSLError:
                    LOG.debug("SSL error", exc_info=True)
                    return Result(
                        ResultType.FAILED, "SSL error, failed to connect to remote."
                    )
            LOG.info("Exception info", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        spaces = self.deployment.network_mapping.values()
        spaces = [space for space in spaces if space is not None]
        if len(spaces) > 0:
            try:
                maas_spaces = list_spaces(client)
            except ValueError as e:
                LOG.debug("Failed to list spaces", exc_info=True)
                return Result(ResultType.FAILED, str(e))
            maas_spaces = [maas_space["name"] for maas_space in maas_spaces]
            difference = set(spaces).difference(maas_spaces)
            if len(difference) > 0:
                return Result(
                    ResultType.FAILED,
                    f"Spaces {', '.join(difference)} not found in MAAS.",
                )

        if (
            self.deployment.juju_controller is None
            and self.deployment.juju_account is not None  # noqa: W503
        ):
            return Result(
                ResultType.FAILED,
                "Juju account configured, but Juju Controller not configured.",
            )

        if (
            self.deployment.juju_controller is not None
            and self.deployment.juju_account is None  # noqa: W503
        ):
            return Result(
                ResultType.FAILED,
                "Juju Controller configured, but Juju account not configured.",
            )

        if (
            self.deployment.juju_account is not None
            and self.deployment.juju_controller is not None  # noqa: W503
        ):
            controller = self.deployment.get_connected_controller()
            try:
                run_sync(controller.list_models())
            except Exception as e:
                LOG.debug("Failed to list models", exc_info=True)
                return Result(ResultType.FAILED, str(e))

        self.deployments_config.add_deployment(self.deployment)
        return Result(ResultType.COMPLETED)


class MachineRolesCheck(DiagnosticsCheck):
    """Check machine has roles assigned."""

    def __init__(self, machine: dict):
        super().__init__(
            "Role check",
            "Checking roles",
        )
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        assigned_roles = self.machine["roles"]
        LOG.debug(f"{self.machine['hostname']=!r} assigned roles: {assigned_roles!r}")
        if not assigned_roles:
            return DiagnosticsResult(
                self.name,
                False,
                "machine has no role assigned.",
                diagnostics=ROLES_NEEDED_ERROR,
                machine=self.machine["hostname"],
            )

        return DiagnosticsResult(
            self.name,
            True,
            ", ".join(self.machine["roles"]),
            machine=self.machine["hostname"],
        )


class MachineNetworkCheck(DiagnosticsCheck):
    """Check machine has the right networks assigned."""

    def __init__(self, deployment: MaasDeployment, machine: dict):
        super().__init__(
            "Network check",
            "Checking networks",
        )
        self.deployment = deployment
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        """Check machine has access to required networks."""
        network_to_space_mapping = get_network_mapping(self.deployment)
        spaces = network_to_space_mapping.values()
        if len(spaces) != len(Networks.values()) or not all(spaces):
            return DiagnosticsResult.fail(
                self.name,
                "network mapping is incomplete",
                diagnostics=textwrap.dedent(
                    """\
                    A complete map of networks to spaces is required to proceed.
                    Complete network mapping to using `sunbeam deployment space map...`.
                    """
                ),
                machine=self.machine["hostname"],
            )
        assigned_roles = self.machine["roles"]
        LOG.debug(f"{self.machine['hostname']=!r} assigned roles: {assigned_roles!r}")
        if not assigned_roles:
            return DiagnosticsResult.fail(
                self.name,
                "machine has no role assigned",
                diagnostics=ROLES_NEEDED_ERROR,
                machine=self.machine["hostname"],
            )
        assigned_spaces = self.machine["spaces"]
        LOG.debug(f"{self.machine['hostname']=!r} assigned spaces: {assigned_spaces!r}")
        required_networks: set[Networks] = set()
        for role in assigned_roles:
            required_networks.update(ROLE_NETWORK_MAPPING[RoleTags(role)])
        LOG.debug(
            f"{self.machine['hostname']=!r} required networks: {required_networks!r}"
        )
        required_spaces = set()
        missing_spaces = set()
        for network in required_networks:
            corresponding_space = network_to_space_mapping[network.value]
            required_spaces.add(corresponding_space)
            if corresponding_space not in assigned_spaces:
                missing_spaces.add(corresponding_space)
        LOG.debug(f"{self.machine['hostname']=!r} missing spaces: {missing_spaces!r}")
        if not assigned_spaces or missing_spaces:
            return DiagnosticsResult.fail(
                self.name,
                f"missing {', '.join(missing_spaces)}",
                diagnostics=textwrap.dedent(
                    f"""\
                    A machine needs to be in spaces to be a part of an openstack
                    deployment. Given machine has roles: {', '.join(assigned_roles)},
                    and therefore needs to be a part of the following spaces:
                    {', '.join(required_spaces)}."""
                ),
                machine=self.machine["hostname"],
            )
        return DiagnosticsResult.success(
            self.name,
            ", ".join(assigned_spaces),
            machine=self.machine["hostname"],
        )


class MachineStorageCheck(DiagnosticsCheck):
    """Check machine has storage assigned if required."""

    def __init__(self, machine: dict):
        super().__init__(
            "Storage check",
            "Checking storage",
        )
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        """Check machine has storage assigned if required."""
        assigned_roles = self.machine["roles"]
        LOG.debug(f"{self.machine['hostname']=!r} assigned roles: {assigned_roles!r}")
        if not assigned_roles:
            return DiagnosticsResult.fail(
                self.name,
                "machine has no role assigned.",
                ROLES_NEEDED_ERROR,
                machine=self.machine["hostname"],
            )
        if RoleTags.STORAGE.value not in assigned_roles:
            self.message = "not a storage node."
            return DiagnosticsResult.success(
                self.name,
                self.message,
                machine=self.machine["hostname"],
            )
        # TODO(gboutry): check number of storage ?
        if len(self.machine["storage"].get(StorageTags.CEPH.value, [])) < 1:
            return DiagnosticsResult.fail(
                self.name,
                "storage node has no ceph storage",
                textwrap.dedent(
                    f"""\
                    A storage node needs to have ceph storage to be a part of
                    an openstack deployment. Either add ceph storage to the
                    machine or remove the storage role. Add the tag
                    `{StorageTags.CEPH.value}` to the storage device in MAAS.
                    More on assigning tags: https://maas.io/docs/using-storage-tags"""
                ),
                machine=self.machine["hostname"],
            )
        return DiagnosticsResult.success(
            self.name,
            ", ".join(
                f"{tag}({len(devices)})"
                for tag, devices in self.machine["storage"].items()
            ),
            machine=self.machine["hostname"],
        )


class MachineRequirementsCheck(DiagnosticsCheck):
    """Check machine meets requirements."""

    def __init__(self, machine: dict):
        super().__init__(
            "Machine requirements check",
            "Checking machine requirements",
        )
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        """Check machine meets requirements."""
        if [RoleTags.JUJU_CONTROLLER.value] == self.machine["roles"]:
            memory_min = RAM_4_GB_IN_MB
            core_min = 2
        else:
            memory_min = RAM_32_GB_IN_MB
            core_min = 16
        if self.machine["memory"] < memory_min or self.machine["cores"] < core_min:
            return DiagnosticsResult.fail(
                self.name,
                "machine does not meet requirements",
                textwrap.dedent(
                    f"""\
                    A machine needs to have at least {core_min} cores and
                    {memory_min}MB RAM to be a part of an openstack deployment.
                    Either add more cores and memory to the machine or remove the
                    machine from the deployment.
                    {self.machine['hostname']}:
                        roles: {self.machine["roles"]}
                        cores: {self.machine["cores"]}
                        memory: {self.machine["memory"]}MB"""
                ),
                machine=self.machine["hostname"],
            )

        return DiagnosticsResult.success(
            self.name,
            f"{self.machine['cores']} cores, {self.machine['memory']}MB RAM",
            machine=self.machine["hostname"],
        )


def str_presenter(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    """Return multiline string as '|' literal block.

    Ref: https://stackoverflow.com/questions/8640959/how-can-i-control-what-scalar-form-pyyaml-uses-for-my-data # noqa E501
    """
    if data.count("\n") > 0:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _run_check_list(checks: list[DiagnosticsCheck]) -> list[DiagnosticsResult]:
    check_results = []
    for check in checks:
        LOG.debug(f"Starting check {check.name}")
        results = check.run()
        if isinstance(results, DiagnosticsResult):
            results = [results]
        for result in results:
            LOG.debug(f"{result.name=!r}, {result.passed=!r}, {result.message=!r}")
            check_results.extend(results)
    return check_results


class DeploymentMachinesCheck(DiagnosticsCheck):
    """Check all machines inside deployment."""

    def __init__(self, deployment: MaasDeployment, machines: list[dict]):
        super().__init__(
            "Deployment check",
            "Checking machines, roles, networks and storage",
        )
        self.deployment = deployment
        self.machines = machines

    def run(self) -> list[DiagnosticsResult]:
        """Run a series of checks on the machines' definition."""
        checks = []
        for machine in self.machines:
            checks.append(MachineRolesCheck(machine))
            checks.append(MachineNetworkCheck(self.deployment, machine))
            checks.append(MachineStorageCheck(machine))
            checks.append(MachineRequirementsCheck(machine))
        results = _run_check_list(checks)
        results.append(
            DiagnosticsResult(self.name, all(result.passed for result in results))
        )
        return results


class DeploymentRolesCheck(DiagnosticsCheck):
    """Check deployment as enough nodes with given role."""

    def __init__(
        self, machines: list[dict], role_name: str, role_tag: str, min_count: int = 3
    ):
        super().__init__(
            "Minimum role check",
            "Checking minimum number of machines with given role",
        )
        self.machines = machines
        self.role_name = role_name
        self.role_tag = role_tag
        self.min_count = min_count

    def run(self) -> DiagnosticsResult:
        """Checks if there's enough machines with given role."""
        machines = 0
        for machine in self.machines:
            if self.role_tag in machine["roles"]:
                machines += 1
        if machines < self.min_count:
            return DiagnosticsResult.fail(
                self.name,
                "less than 3 " + self.role_name,
                textwrap.dedent(
                    """\
                    A deployment needs to have at least {min_count} {role_name} to be
                    a part of an openstack deployment. You need to add more {role_name}
                    to the deployment using {role_tag} tag.
                    More on using tags: https://maas.io/docs/using-machine-tags
                    """.format(
                        min_count=self.min_count,
                        role_name=self.role_name,
                        role_tag=self.role_tag,
                    )
                ),
            )
        return DiagnosticsResult.success(
            self.name,
            f"{self.role_name}: {machines}",
        )


class ZonesCheck(DiagnosticsCheck):
    """Check that there either 1 zone or more than 2 zones."""

    def __init__(self, zones: list[str]):
        super().__init__(
            "Zone check",
            "Checking zones",
        )
        self.zones = zones

    def run(self) -> DiagnosticsResult:
        """Checks deployment zones."""
        if len(self.zones) == 2:
            return DiagnosticsResult.fail(
                self.name,
                "deployment has 2 zones",
                textwrap.dedent(
                    f"""\
                    A deployment needs to have either 1 zone or more than 2 zones.
                    Current zones: {', '.join(self.zones)}"""
                ),
            )
        return DiagnosticsResult.success(
            self.name,
            f"{len(self.zones)} zone(s)",
        )


class ZoneBalanceCheck(DiagnosticsCheck):
    """Check that roles are balanced throughout zones."""

    def __init__(self, machines: dict[str, list[dict]]):
        super().__init__(
            "Zone balance check",
            "Checking role distribution across zones",
        )
        self.machines = machines

    def run(self) -> DiagnosticsResult:
        """Check role distribution across zones."""
        zone_role_counts = {}
        for zone, machines in self.machines.items():
            zone_role_counts.setdefault(zone, {})
            for machine in machines:
                for role in machine["roles"]:
                    zone_role_counts[zone].setdefault(role, 0)
                    zone_role_counts[zone][role] += 1
        LOG.debug(f"{zone_role_counts=!r}")
        unbalanced_roles = []
        distribution = ""
        for role in RoleTags.values():
            counts = [zone_role_counts[zone].get(role, 0) for zone in zone_role_counts]
            max_count = max(counts)
            min_count = min(counts)
            if max_count != min_count:
                unbalanced_roles.append(role)
            distribution += f"{role}:"
            for zone, counts in zone_role_counts.items():
                distribution += f"\n  {zone}={counts.get(role, 0)}"
            distribution += "\n"

        if unbalanced_roles:
            diagnostics = textwrap.dedent(
                """\
                A deployment needs to have the same number of machines with the same
                role in each zone. Either add more machines to the zones or remove the
                zone from the deployment.
                More on using tags: https://maas.io/docs/using-machine-tags
                Distribution of roles across zones:
                """
            )
            diagnostics += distribution
            return DiagnosticsResult.fail(
                self.name,
                f"{', '.join(unbalanced_roles)} distribution is unbalanced",
                diagnostics,
            )
        return DiagnosticsResult.success(
            self.name,
            "deployment is balanced",
            distribution,
        )


class IpRangesCheck(DiagnosticsCheck):
    """Check IP ranges are complete."""

    _missing_range_diagnostic = textwrap.dedent(
        """\
        IP ranges are required to proceed.
        You need to setup a Reserverd IP Range for any subnet in the
        space ({space!r}) mapped to the {network!r} network.
        Multiple ip ranges can be defined in on the same/different subnets
        in the space. Each of these ip ranges should have as comment:
        {label!r}.

        More on setting up IP ranges:
        https://maas.io/docs/ip-ranges
        """
    )

    def __init__(self, client: MaasClient, deployment: MaasDeployment):
        super().__init__(
            "IP ranges check",
            "Checking IP ranges",
        )
        self.client = client
        self.deployment = deployment

    def _get_ranges_for_label(
        self, subnet_ranges: dict[str, list[dict]], label: str
    ) -> list[tuple]:
        """Ip ranges for a given label."""
        ip_ranges = []
        for ranges in subnet_ranges.values():
            for ip_range in ranges:
                if ip_range["label"] == label:
                    ip_ranges.append((ip_range["start"], ip_range["end"]))

        return ip_ranges

    def run(
        self,
    ) -> DiagnosticsResult:
        """Check Public and Internal ip ranges are set."""

        public_space = self.deployment.network_mapping.get(Networks.PUBLIC.value)
        internal_space = self.deployment.network_mapping.get(Networks.INTERNAL.value)
        if public_space is None or internal_space is None:
            return DiagnosticsResult.fail(
                self.name,
                "IP ranges are not set",
                textwrap.dedent(
                    """\
                    A complete map of networks to spaces is required to proceed.
                    Complete network mapping to using `sunbeam deployment space map...`.
                    """
                ),
            )

        public_subnet_ranges = get_ip_ranges_from_space(self.client, public_space)
        internal_subnet_ranges = get_ip_ranges_from_space(self.client, internal_space)

        public_ip_ranges = self._get_ranges_for_label(
            public_subnet_ranges, MAAS_PUBLIC_IP_RANGE
        )
        internal_ip_ranges = self._get_ranges_for_label(
            internal_subnet_ranges, MAAS_INTERNAL_IP_RANGE
        )
        if len(public_ip_ranges) == 0:
            return DiagnosticsResult.fail(
                self.name,
                "Public IP ranges are not set",
                self._missing_range_diagnostic.format(
                    space=public_space,
                    network=Networks.PUBLIC.value,
                    label=MAAS_PUBLIC_IP_RANGE,
                ),
            )

        if len(internal_ip_ranges) == 0:
            return DiagnosticsResult.fail(
                self.name,
                "Internal IP ranges are not set",
                self._missing_range_diagnostic.format(
                    space=internal_space,
                    network=Networks.INTERNAL.value,
                    label=MAAS_INTERNAL_IP_RANGE,
                ),
            )

        diagnostics = (
            f"Public IP ranges: {public_ip_ranges!r}\n"
            f"Internal IP ranges: {internal_ip_ranges!r}"
        )

        return DiagnosticsResult.success(self.name, "IP ranges are set", diagnostics)


class DeploymentTopologyCheck(DiagnosticsCheck):
    """Check deployment topology."""

    def __init__(self, machines: list[dict]):
        super().__init__(
            "Topology check",
            "Checking zone distribution",
        )
        self.machines = machines

    def run(self) -> list[DiagnosticsResult]:
        """Run a sequence of checks to validate deployment topology.""" ""
        machines_by_zone = _group_machines_by_zone(self.machines)
        checks = []
        checks.append(
            DeploymentRolesCheck(
                self.machines, "juju controllers", RoleTags.JUJU_CONTROLLER.value
            )
        )
        checks.append(
            DeploymentRolesCheck(self.machines, "control nodes", RoleTags.CONTROL.value)
        )
        checks.append(
            DeploymentRolesCheck(self.machines, "compute nodes", RoleTags.COMPUTE.value)
        )
        checks.append(
            DeploymentRolesCheck(self.machines, "storage nodes", RoleTags.STORAGE.value)
        )
        checks.append(ZonesCheck(list(machines_by_zone.keys())))
        checks.append(ZoneBalanceCheck(machines_by_zone))

        results = _run_check_list(checks)
        results.append(
            DiagnosticsResult(self.name, all(result.passed for result in results))
        )
        return results


class DeploymentNetworkingCheck(DiagnosticsCheck):
    """Check deployment networking."""

    def __init__(self, client: MaasClient, deployment: MaasDeployment):
        super().__init__(
            "Networking check",
            "Checking networking",
        )
        self.client = client
        self.deployment = deployment

    def run(self) -> list[DiagnosticsResult]:
        """Run a sequence of checks to validate deployment networking."""
        checks = []
        checks.append(IpRangesCheck(self.client, self.deployment))

        results = _run_check_list(checks)
        results.append(
            DiagnosticsResult(self.name, all(result.passed for result in results))
        )
        return results


class NetworkMappingCompleteCheck(Check):
    """Check network mapping is complete."""

    def __init__(self, deployment: MaasDeployment):
        super().__init__(
            "NetworkMapping Check",
            "Checking network mapping is complete",
        )
        self.deployment = deployment

    def run(self) -> bool:
        """Check network mapping is complete."""
        network_to_space_mapping = self.deployment.network_mapping
        spaces = network_to_space_mapping.values()
        if len(spaces) != len(Networks.values()) or not all(spaces):
            self.message = (
                "A complete map of networks to spaces is required to proceed."
                " Complete network mapping to using `sunbeam deployment space map...`."
            )
            return False
        return True


class MaasBootstrapJujuStep(BootstrapJujuStep):
    """Bootstrap the Juju controller."""

    def __init__(
        self,
        maas_client: MaasClient,
        cloud: str,
        cloud_type: str,
        controller: str,
        password: str,
        bootstrap_args: list[str] | None = None,
        deployment_preseed: dict | None = None,
        accept_defaults: bool = False,
    ):
        bootstrap_args = bootstrap_args or []
        bootstrap_args.extend(
            (
                "--bootstrap-constraints",
                f"tags={RoleTags.JUJU_CONTROLLER.value}",
                "--bootstrap-base",
                "ubuntu@22.04",
                "--config",
                f"admin-secret={password}",
            )
        )
        super().__init__(
            # client is not used when bootstrapping with maas,
            # as it was used during prompts and there's no prompt with maas
            None,  # type: ignore
            cloud,
            cloud_type,
            controller,
            bootstrap_args,
            deployment_preseed,
            accept_defaults,
        )
        self.maas_client = maas_client

    def prompt(self, console: Console | None = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return False

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        machines = list_machines(self.maas_client, tags=RoleTags.JUJU_CONTROLLER.value)
        if len(machines) == 0:
            return Result(
                ResultType.FAILED,
                f"No machines with tag {RoleTags.JUJU_CONTROLLER.value!r} found.",
            )
        controller = sorted(machines, key=lambda x: x["hostname"])[0]
        self.bootstrap_args.extend(("--to", "system-id=" + controller["system_id"]))
        return super().is_skip(status)


class MaasScaleJujuStep(ScaleJujuStep):
    """Scale Juju Controller on MAAS deployment."""

    def __init__(
        self,
        maas_client: MaasClient,
        controller: str,
        extra_args: list[str] | None = None,
    ):
        extra_args = extra_args or []
        extra_args.extend(
            (
                "--constraints",
                f"tags={RoleTags.JUJU_CONTROLLER.value}",
            )
        )
        super().__init__(controller, extra_args=extra_args)
        self.client = maas_client

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        try:
            controller = self.get_controller(self.controller)
        except ControllerNotFoundException as e:
            LOG.debug(str(e))
            return Result(ResultType.FAILED, f"Controller {self.controller} not found")

        controller_machines = controller.get("controller-machines")
        if controller_machines is None:
            return Result(
                ResultType.FAILED,
                f"Controller {self.controller} has no machines registered.",
            )
        nb_controllers = len(controller_machines)

        if nb_controllers == self.n:
            LOG.debug("Already the correct number of controllers, skipping scaling...")
            return Result(ResultType.SKIPPED)

        if nb_controllers > self.n:
            return Result(
                ResultType.FAILED,
                f"Can't scale down controllers from {nb_controllers} to {self.n}.",
            )

        machines = list_machines(self.client, tags=RoleTags.JUJU_CONTROLLER.value)

        if len(machines) < self.n:
            LOG.debug(
                f"Found {len(machines)} juju controllers,"
                f" need {self.n} to scale, skipping..."
            )
            return Result(ResultType.SKIPPED)
        machines = sorted(machines, key=lambda x: x["hostname"])

        system_ids = [machine["system_id"] for machine in machines]
        for controller_machine in controller_machines.values():
            if controller_machine["instance-id"] in system_ids:
                system_ids.remove(controller_machine["instance-id"])

        placement = ",".join(f"system-id={system_id}" for system_id in system_ids)

        self.extra_args.extend(("--to", placement))
        return Result(ResultType.COMPLETED)


class MaasSaveControllerStep(BaseStep, JujuStepHelper):
    """Save maas controller information locally."""

    def __init__(
        self,
        controller: str,
        deployment_name: str,
        deployments_config: DeploymentsConfig,
    ):
        super().__init__(
            "Save controller information",
            "Saving controller information locally",
        )
        self.controller = controller
        self.deployment_name = deployment_name
        self.deployments_config = deployments_config

    def _get_controller(self, name: str) -> JujuController | None:
        try:
            controller = self.get_controller(name)["details"]
        except ControllerNotFoundException as e:
            LOG.debug(str(e))
            return None
        return JujuController(
            api_endpoints=controller["api-endpoints"],
            ca_cert=controller["ca-cert"],
        )

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        deployment = self.deployments_config.get_deployment(self.deployment_name)
        if not is_maas_deployment(deployment):
            return Result(ResultType.SKIPPED)
        if deployment.juju_controller is None:
            return Result(ResultType.COMPLETED)

        controller = self._get_controller(self.controller)
        if controller is None:
            return Result(ResultType.FAILED, f"Controller {self.controller} not found")

        if controller == deployment.juju_controller:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Save controller to deployment information."""
        controller = self._get_controller(self.controller)
        if controller is None:
            return Result(ResultType.FAILED, f"Controller {self.controller} not found")

        deployment = self.deployments_config.get_deployment(self.deployment_name)
        if not is_maas_deployment(deployment):
            return Result(ResultType.FAILED)

        deployment.juju_controller = controller
        self.deployments_config.write()
        return Result(ResultType.COMPLETED)


class MaasSaveClusterdAddressStep(BaseStep):
    """Save clusterd address locally."""

    def __init__(
        self,
        jhelper: JujuHelper,
        deployment_name: str,
        deployments_config: DeploymentsConfig,
    ):
        super().__init__(
            "Save clusterd address",
            "Saving clusterd address locally",
        )
        self.jhelper = jhelper
        self.deployment_name = deployment_name
        self.deployments_config = deployments_config

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        deployment = self.deployments_config.get_deployment(self.deployment_name)
        if not is_maas_deployment(deployment):
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Save clusterd address to deployment information."""

        async def _get_credentials() -> dict:
            leader_unit = await self.jhelper.get_leader_unit(
                CLUSTERD_APPLICATION, "controller"
            )
            result = await self.jhelper.run_action(
                leader_unit, "controller", "get-credentials"
            )
            if result.get("return-code", 0) > 1:
                raise ValueError("Failed to retrieve credentials")
            return result

        try:
            credentials = run_sync(_get_credentials())
        except (LeaderNotFoundException, ValueError, ActionFailedException) as e:
            return Result(ResultType.FAILED, str(e))

        url = credentials.get("url")
        if url is None:
            return Result(ResultType.FAILED, "Failed to retrieve clusterd url")

        client = Client.from_http(url)
        try:
            client.cluster.list_nodes()
        except Exception as e:
            return Result(ResultType.FAILED, str(e))
        deployment = self.deployments_config.get_deployment(self.deployment_name)
        if not is_maas_deployment(deployment):
            return Result(ResultType.FAILED)

        deployment.clusterd_address = url
        self.deployments_config.write()
        return Result(ResultType.COMPLETED)


class MaasAddMachinesToClusterdStep(BaseStep):
    """Add machines from MAAS to Clusterd."""

    def __init__(self, client: Client, maas_client: MaasClient):
        super().__init__("Add machines", "Adding machines to Clusterd")
        self.client = client
        self.maas_client = maas_client
        self.machines = None
        self.nodes = None

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        maas_machines = list_machines(self.maas_client)
        LOG.debug(f"Machines fetched: {maas_machines}")
        filtered_machines = []
        for machine in maas_machines:
            if set(machine["roles"]).intersection(
                {
                    RoleTags.CONTROL.value,
                    RoleTags.COMPUTE.value,
                    RoleTags.STORAGE.value,
                }
            ):
                filtered_machines.append(machine)
        LOG.debug(f"Machines containing worker roles: {filtered_machines}")
        if filtered_machines is None or len(filtered_machines) == 0:
            return Result(ResultType.FAILED, "Maas deployment has no machines.")
        clusterd_nodes = self.client.cluster.list_nodes()
        nodes_to_update = []
        for node in clusterd_nodes:
            for machine in filtered_machines:
                if node["name"] == machine["hostname"]:
                    filtered_machines.remove(machine)
                    if sorted(node["role"]) != sorted(machine["roles"]):
                        nodes_to_update.append((machine["hostname"], machine["roles"]))
        self.nodes = nodes_to_update
        self.machines = filtered_machines
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Add machines to Juju model."""
        if self.machines is None or self.nodes is None:
            # only happens if is_skip() was not called before, or if run executed
            # even if is_skip reported a failure
            return Result(ResultType.FAILED, "No machines to add / node to update.")
        for machine in self.machines:
            self.client.cluster.add_node_info(
                machine["hostname"], machine["roles"], systemid=machine["system_id"]
            )
        for node in self.nodes:
            self.client.cluster.update_node_info(*node)
        return Result(ResultType.COMPLETED)


class MaasDeployMachinesStep(BaseStep):
    """Deploy machines stored in Clusterd in Juju."""

    def __init__(
        self, client: Client, jhelper: JujuHelper, model: str = INFRASTRUCTURE_MODEL
    ):
        super().__init__("Deploy machines", "Deploying machines in Juju")
        self.client = client
        self.jhelper = jhelper
        self.model = model

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        clusterd_nodes = self.client.cluster.list_nodes()
        if len(clusterd_nodes) == 0:
            return Result(ResultType.FAILED, "No machines to deploy.")

        juju_machines = run_sync(self.jhelper.get_machines(self.model))

        nodes_to_deploy = clusterd_nodes.copy()
        nodes_to_update = []
        for node in clusterd_nodes:
            node_machine_id = node["machineid"]
            for id, machine in juju_machines.items():
                if node["name"] == machine.hostname:
                    if int(id) != node_machine_id and node_machine_id != -1:
                        return Result(
                            ResultType.FAILED,
                            f"Machine {node['name']} already exists in model"
                            f" {self.model} with id {id},"
                            f" expected the id {node['machineid']}.",
                        )
                    if (
                        node["systemid"] != machine.instance_id
                        and node["systemid"] != ""  # noqa: W503
                    ):
                        return Result(
                            ResultType.FAILED,
                            f"Machine {node['name']} already exists in model"
                            f" {self.model} with systemid {machine.instance_id},"
                            f" expected the systemid {node['systemid']}.",
                        )
                    if node_machine_id == -1:
                        nodes_to_update.append(node)
                    nodes_to_deploy.remove(node)
                    break

        self.nodes_to_deploy = sorted(nodes_to_deploy, key=lambda x: x["name"])
        self.nodes_to_update = nodes_to_update

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Deploy machines in Juju."""
        for node in self.nodes_to_deploy:
            self.update_status(status, f"deploying {node['name']}")
            LOG.debug(f"Adding machine {node['name']} to model {self.model}")
            juju_machine = run_sync(
                self.jhelper.add_machine("system-id=" + node["systemid"], self.model)
            )
            self.client.cluster.update_node_info(
                node["name"], machineid=int(juju_machine.id)
            )
        self.update_status(status, "waiting for machines to deploy")
        model = run_sync(self.jhelper.get_model(self.model))
        for node in self.nodes_to_update:
            LOG.debug(f"Updating machine {node['name']} in model {self.model}")
            for juju_machine in model.machines.values():
                if juju_machine is None:
                    continue
                if juju_machine.hostname == node["name"]:
                    self.client.cluster.update_node_info(
                        node["name"], machineid=int(juju_machine.id)
                    )
                    break
        try:
            run_sync(self.jhelper.wait_all_machines_deployed(self.model))
        except TimeoutException:
            LOG.debug("Timeout waiting for machines to deploy", exc_info=True)
            return Result(ResultType.FAILED, "Timeout waiting for machines to deploy.")
        return Result(ResultType.COMPLETED)


class MaasConfigureMicrocephOSDStep(BaseStep):
    """Configure Microceph OSD disks"""

    def __init__(
        self,
        client: Client,
        maas_client: MaasClient,
        jhelper: JujuHelper,
        names: list[str],
        model: str = INFRASTRUCTURE_MODEL,
    ):
        super().__init__("Configure MicroCeph storage", "Configuring MicroCeph storage")
        self.client = client
        self.maas_client = maas_client
        self.jhelper = jhelper
        self.names = names
        self.model = model
        self.disks_to_configure: dict[str, list[str]] = {}

    async def _list_disks(self, unit: str) -> tuple[dict, dict]:
        """Call list-disks action on an unit."""
        LOG.debug("Running list-disks on : %r", unit)
        action_result = await self.jhelper.run_action(unit, self.model, "list-disks")
        LOG.debug(
            "Result after running action list-disks on %r: %r",
            unit,
            action_result,
        )
        osds = ast.literal_eval(action_result.get("osds", "[]"))
        unpartitioned_disks = ast.literal_eval(
            action_result.get("unpartitioned-disks", "[]")
        )
        return osds, unpartitioned_disks

    async def _get_microceph_disks(self) -> dict:
        """Retrieve all disks added to microceph.

        Return a dict of format:
            {
                "<machine>": {
                    "osds": ["<disk1_path>", "<disk2_path>"],
                    "unpartitioned_disks": ["<disk3_path>"]
                    "unit": "<unit_name>"
                }
            }
        """
        try:
            leader = await self.jhelper.get_leader_unit(
                microceph.APPLICATION, self.model
            )
        except LeaderNotFoundException as e:
            LOG.debug("Failed to find leader unit", exc_info=True)
            raise ValueError(str(e))
        osds, _ = await self._list_disks(leader)
        disks = {}
        default_disk = {"osds": [], "unpartitioned_disks": []}
        for osd in osds:
            location = osd["location"]  # machine name
            disks.setdefault(location, copy.deepcopy(default_disk))["osds"].append(
                osd["path"]
            )

        for name in self.names:
            machine_id = str(self.client.cluster.get_node_info(name)["machineid"])
            unit = await self.jhelper.get_unit_from_machine(
                microceph.APPLICATION, machine_id, self.model
            )
            if unit is None:
                raise ValueError(
                    f"{microceph.APPLICATION}'s unit not found on {name}."
                    " Is microceph deployed on this machine?"
                )
            _, unit_unpartitioned_disks = await self._list_disks(unit.entity_id)
            disks.setdefault(name, copy.deepcopy(default_disk))[
                "unpartitioned_disks"
            ].extend(uud["path"] for uud in unit_unpartitioned_disks)
            disks[name]["unit"] = unit.entity_id

        return disks

    def _get_maas_disks(self) -> dict:
        """Retrieve all disks from MAAS per machine.

        Return a dict of format:
            {
                "<machine>": ["<disk1_path>", "<disk2_path>"]
            }
        """
        machines = list_machines(self.maas_client, hostname=self.names)
        disks = {}
        for machine in machines:
            disks[machine["hostname"]] = [
                device["id_path"]
                for device in machine["storage"][StorageTags.CEPH.value]
            ]

        return disks

    def _compute_disks_to_configure(
        self, microceph_disks: dict, maas_disks: set[str]
    ) -> list[str]:
        """Compute the disks that need to be configured for the machine."""
        machine_osds = set(microceph_disks["osds"])
        machine_unpartitioned_disks = set(microceph_disks["unpartitioned_disks"])
        machine_unit = microceph_disks["unit"]
        if len(maas_disks) == 0:
            raise ValueError(
                f"Machine {machine_unit!r} does not have any"
                f" {StorageTags.CEPH.value!r} disk defined."
            )
        # Get all disks that are in Ceph but not in MAAS
        unknown_osds = machine_osds - maas_disks
        # Get all disks that are in MAAS but neither in Ceph nor unpartitioned
        missing_disks = maas_disks - machine_osds - machine_unpartitioned_disks
        # Disks to partition
        disks_to_configure = maas_disks.intersection(machine_unpartitioned_disks)

        if len(unknown_osds) > 0:
            raise ValueError(
                f"Machine {machine_unit!r} has OSDs from disks unknown to MAAS:"
                f" {unknown_osds}"
            )
        if len(missing_disks) > 0:
            raise ValueError(
                f"Machine {machine_unit!r} is missing disks: {missing_disks}"
            )
        if len(disks_to_configure) > 0:
            LOG.debug(
                "Unit %r will configure the following disks: %r",
                machine_unit,
                disks_to_configure,
            )
            return list(disks_to_configure)

        return []

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        try:
            microceph_disks = run_sync(self._get_microceph_disks())
            LOG.debug("Computing disk mapping: %r", microceph_disks)
        except ValueError as e:
            LOG.debug("Failed to list microceph disks from units", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        try:
            maas_disks = self._get_maas_disks()
            LOG.debug("Maas disks: %r", maas_disks)
        except ValueError as e:
            LOG.debug("Failed to list disks from MAAS", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        disks_to_configure: dict[str, list[str]] = {}

        for name in self.names:
            try:
                machine_disks_to_configure = self._compute_disks_to_configure(
                    microceph_disks[name], set(maas_disks.get(name, []))
                )
            except ValueError as e:
                LOG.debug(
                    "Failed to compute disks to configure for machine %r",
                    name,
                    exc_info=True,
                )
                return Result(ResultType.FAILED, str(e))
            if len(machine_disks_to_configure) > 0:
                disks_to_configure[microceph_disks[name]["unit"]] = (
                    machine_disks_to_configure
                )

        if len(disks_to_configure) == 0:
            LOG.debug("No disks to configure, skipping step.")
            return Result(ResultType.SKIPPED)

        self.disks_to_configure = disks_to_configure
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Configure local disks on microceph."""

        for unit, disks in self.disks_to_configure.items():
            try:
                LOG.debug("Running action add-osd on %r", unit)
                action_result = run_sync(
                    self.jhelper.run_action(
                        unit,
                        self.model,
                        "add-osd",
                        action_params={
                            "device-id": ",".join(disks),
                        },
                    )
                )
                LOG.debug(
                    "Result after running action add-osd on %r: %r", unit, action_result
                )
            except (UnitNotFoundException, ActionFailedException) as e:
                LOG.debug("Failed to run action add-osd on %r", unit, exc_info=True)
                return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class MaasDeployMicrok8sApplicationStep(microk8s.DeployMicrok8sApplicationStep):
    """Deploy Microk8s application using Terraform"""

    def __init__(
        self,
        client: Client,
        maas_client: MaasClient,
        manifest: Manifest,
        jhelper: JujuHelper,
        public_space: str,
        internal_space: str,
        model: str = INFRASTRUCTURE_MODEL,
        deployment_preseed: dict | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__(
            client,
            manifest,
            jhelper,
            model,
            deployment_preseed,
            accept_defaults,
        )
        self.maas_client = maas_client
        self.public_space = public_space
        self.internal_space = internal_space
        self.ranges = None

    def extra_tfvars(self) -> dict:
        if self.ranges is None:
            raise ValueError("No ip ranges found")
        return {"addons": {"dns": "", "hostpath-storage": "", "metallb": self.ranges}}

    def prompt(self, console: Console | None = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return False

    def _to_joined_range(self, subnet_ranges: dict[str, list[dict]], label: str) -> str:
        """Convert a list of ip ranges to a string for cni config.

        Current cni config format is: <ip start>-<ip end>,<ip  start>-<ip end>,...
        """
        mettallb_range = []
        for ip_ranges in subnet_ranges.values():
            for ip_range in ip_ranges:
                if ip_range["label"] == label:
                    mettallb_range.append(f"{ip_range['start']}-{ip_range['end']}")
        if len(mettallb_range) == 0:
            raise ValueError("No ip range found for label: " + label)
        return ",".join(mettallb_range)

    def is_skip(self, status: Status | None = None):
        """Determines if the step should be skipped or not."""
        try:
            public_ranges = get_ip_ranges_from_space(
                self.maas_client, self.public_space
            )
            LOG.debug("Public ip ranges: %r", public_ranges)
        except ValueError as e:
            LOG.debug(
                "Failed to ip ranges for space: %r", self.public_space, exc_info=True
            )
            return Result(ResultType.FAILED, str(e))
        try:
            public_metallb_range = self._to_joined_range(
                public_ranges, MAAS_PUBLIC_IP_RANGE
            )
        except ValueError:
            LOG.debug(
                "No iprange with label %r found", MAAS_PUBLIC_IP_RANGE, exc_info=True
            )
            return Result(ResultType.FAILED, "No public ip range found")
        self.ranges = public_metallb_range

        try:
            internal_ranges = get_ip_ranges_from_space(
                self.maas_client, self.internal_space
            )
            LOG.debug("Internal ip ranges: %r", internal_ranges)
        except ValueError as e:
            LOG.debug(
                "Failed to ip ranges for space: %r", self.internal_space, exc_info=True
            )
            return Result(ResultType.FAILED, str(e))
        try:
            # TODO(gboutry): use this range when cni (or sunbeam) easily supports
            # using different ip pools
            internal_metallb_range = self._to_joined_range(  # noqa
                internal_ranges, MAAS_INTERNAL_IP_RANGE
            )
        except ValueError:
            LOG.debug(
                "No iprange with label %r found", MAAS_PUBLIC_IP_RANGE, exc_info=True
            )
            return Result(ResultType.FAILED, "No internal ip range found")

        return super().is_skip(status)
