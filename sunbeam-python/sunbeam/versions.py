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

OPENSTACK_CHANNEL = "2023.2/edge"
OVN_CHANNEL = "23.09/edge"
RABBITMQ_CHANNEL = "3.12/edge"
TRAEFIK_CHANNEL = "1.0/edge"
MICROCEPH_CHANNEL = "latest/edge"
SUNBEAM_MACHINE_CHANNEL = "latest/edge"
MICROK8S_CHANNEL = "legacy/stable"
MYSQL_CHANNEL = "8.0/candidate"
CERT_AUTH_CHANNEL = "latest/beta"

# The lists of services are needed for switching charm channels outside
# of the terraform provider. If it ok to upgrade in one big-bang and
# the juju terraform provider supports it then the upgrades can be
# done by simply updating the tfvars and these lists are not needed.
OPENSTACK_SERVICES_K8S = {
    "cinder-ceph": OPENSTACK_CHANNEL,
    "cinder": OPENSTACK_CHANNEL,
    "glance": "2023.2/candidate",
    "horizon": OPENSTACK_CHANNEL,
    "keystone": OPENSTACK_CHANNEL,
    "neutron": OPENSTACK_CHANNEL,
    "nova": OPENSTACK_CHANNEL,
    "placement": OPENSTACK_CHANNEL,
}
OVN_SERVICES_K8S = {
    "ovn-central": OVN_CHANNEL,
    "ovn-relay": OVN_CHANNEL,
}
MYSQL_SERVICES_K8S = {
    "mysql": MYSQL_CHANNEL,
    "cinder-ceph-mysql-router": MYSQL_CHANNEL,
    "cinder-mysql-router": MYSQL_CHANNEL,
    "glance-mysql-router": MYSQL_CHANNEL,
    "horizon-mysql-router": MYSQL_CHANNEL,
    "keystone-mysql-router": MYSQL_CHANNEL,
    "neutron-mysql-router": MYSQL_CHANNEL,
    "nova-api-mysql-router": MYSQL_CHANNEL,
    "nova-cell-mysql-router": MYSQL_CHANNEL,
    "nova-mysql-router": MYSQL_CHANNEL,
    "placement-mysql-router": MYSQL_CHANNEL,
}
MISC_SERVICES_K8S = {
    "certificate-authority": CERT_AUTH_CHANNEL,
    "rabbitmq": RABBITMQ_CHANNEL,
    "traefik": TRAEFIK_CHANNEL,
    "traefik-public": TRAEFIK_CHANNEL,
}
MACHINE_SERVICES = {
    "microceph": MICROCEPH_CHANNEL,
    "microk8s": MICROK8S_CHANNEL,
    "openstack-hypervisor": OPENSTACK_CHANNEL,
    "sunbeam-machine": SUNBEAM_MACHINE_CHANNEL,
}

K8S_SERVICES = {}
K8S_SERVICES |= OPENSTACK_SERVICES_K8S
K8S_SERVICES |= OVN_SERVICES_K8S
K8S_SERVICES |= MYSQL_SERVICES_K8S
K8S_SERVICES |= MISC_SERVICES_K8S

CHARM_VERSIONS = {}
CHARM_VERSIONS |= K8S_SERVICES
CHARM_VERSIONS |= MACHINE_SERVICES
