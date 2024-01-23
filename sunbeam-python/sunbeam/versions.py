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
SUNBEAM_MACHINE_CHANNEL = "2023.2/edge"
MICROK8S_CHANNEL = "legacy/stable"
MYSQL_CHANNEL = "8.0/candidate"
CERT_AUTH_CHANNEL = "latest/beta"
BIND_CHANNEL = "9/edge"
VAULT_CHANNEL = "latest/edge"

# The lists of services are needed for switching charm channels outside
# of the terraform provider. If it ok to upgrade in one big-bang and
# the juju terraform provider supports it then the upgrades can be
# done by simply updating the tfvars and these lists are not needed.
OPENSTACK_SERVICES_K8S = {
    "cinder-ceph": OPENSTACK_CHANNEL,
    "cinder": OPENSTACK_CHANNEL,
    "glance": OPENSTACK_CHANNEL,
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
MYSQL_SERVICES_K8S = {"mysql": MYSQL_CHANNEL}
MYSQL_ROUTER_SERVICES_K8S = {
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
K8S_SERVICES |= MYSQL_ROUTER_SERVICES_K8S
K8S_SERVICES |= MISC_SERVICES_K8S

CHARM_VERSIONS = {}
CHARM_VERSIONS |= K8S_SERVICES
CHARM_VERSIONS |= MACHINE_SERVICES

# Similar to CHARM_VERSIONS except this is not per service
# but per charm. So all *-mysql-router wont be included
# and instead only mysql-router is included. Same is the
# case of traefik charm.
OPENSTACK_CHARMS_K8S = {
    "cinder-ceph-k8s": OPENSTACK_CHANNEL,
    "cinder-k8s": OPENSTACK_CHANNEL,
    "glance-k8s": OPENSTACK_CHANNEL,
    "horizon-k8s": OPENSTACK_CHANNEL,
    "keystone-k8s": OPENSTACK_CHANNEL,
    "neutron-k8s": OPENSTACK_CHANNEL,
    "nova-k8s": OPENSTACK_CHANNEL,
    "placement-k8s": OPENSTACK_CHANNEL,
}
OVN_CHARMS_K8S = {
    "ovn-central-k8s": OVN_CHANNEL,
    "ovn-relay-k8s": OVN_CHANNEL,
}
MYSQL_CHARMS_K8S = {
    "mysql-k8s": MYSQL_CHANNEL,
    "mysql-router-k8s": MYSQL_CHANNEL,
}
MISC_CHARMS_K8S = {
    "self-signed-certificates": CERT_AUTH_CHANNEL,
    "rabbitmq-k8s": RABBITMQ_CHANNEL,
    "traefik-k8s": TRAEFIK_CHANNEL,
}
MACHINE_CHARMS = {
    "microceph": MICROCEPH_CHANNEL,
    "microk8s": MICROK8S_CHANNEL,
    "openstack-hypervisor": OPENSTACK_CHANNEL,
    "sunbeam-machine": SUNBEAM_MACHINE_CHANNEL,
}


K8S_CHARMS = {}
K8S_CHARMS |= OPENSTACK_CHARMS_K8S
K8S_CHARMS |= OVN_CHARMS_K8S
K8S_CHARMS |= MYSQL_CHARMS_K8S
K8S_CHARMS |= MISC_CHARMS_K8S

MANIFEST_CHARM_VERSIONS = {}
MANIFEST_CHARM_VERSIONS |= K8S_CHARMS
MANIFEST_CHARM_VERSIONS |= MACHINE_CHARMS


# <TF plan>: <TF Plan dir>
TERRAFORM_DIR_NAMES = {
    "sunbeam-machine-plan": "deploy-sunbeam-machine",
    "microk8s-plan": "deploy-microk8s",
    "microceph-plan": "deploy-microceph",
    "openstack-plan": "deploy-openstack",
    "hypervisor-plan": "deploy-openstack-hypervisor",
    "demo-setup": "demo-setup",
}


"""
Format of MANIFEST_ATTRIBUTES_TFVAR_MAP
{
    <plan>: {
        "charms": {
            <charm name>: {
                <CharmManifest Attrbiute>: <Terraform variable name>
                ...
                ...
            },
            ...
        },
        "caas_config": {
            <CaasConfig Attribute>: <Terraform variable name>
            ...
            ...
        },
    },
    ...
}

Example:
{
    "openstack-plan": {
        "charms": {
            "keystone-k8s": {
                "channel": "keystone-channel",
                "revision": "keystone-revision",
                "config": "keystone-config"
            },
        },
    },
    "microk8s-plan": {
        "charms": {
            "microk8s": {
                "channel": "charm_microk8s_channel",
                "revision": "charm_microk8s_revision",
                "config": "charm_microk8s_config",
            },
        },
    },
    "caas-setup": {
        "caas_config": {
            "image_name": "image-name",
            "image_url": "image-source-url"
        }
    }
}
"""
DEPLOY_OPENSTACK_TFVAR_MAP = {
    "charms": {
        charm: {
            "channel": f"{charm.removesuffix('-k8s')}-channel",
            "revision": f"{charm.removesuffix('-k8s')}-revision",
            "config": f"{charm.removesuffix('-k8s')}-config",
        }
        for charm, channel in K8S_CHARMS.items()
    }
}
DEPLOY_OPENSTACK_TFVAR_MAP["charms"]["self-signed-certificates"] = {
    "channel": "certificate-authority-channel",
    "revision": "certificate-authority-revision",
    "config": "certificate-authority-config",
}

DEPLOY_MICROK8S_TFVAR_MAP = {
    "charms": {
        "microk8s": {
            "channel": "charm_microk8s_channel",
            "revision": "charm_microk8s_revision",
            "config": "charm_microk8s_config",
        }
    }
}
DEPLOY_MICROCEPH_TFVAR_MAP = {
    "charms": {
        "microceph": {
            "channel": "charm_microceph_channel",
            "revision": "charm_microceph_revision",
            "config": "charm_microceph_config",
        }
    }
}
DEPLOY_OPENSTACK_HYPERVISOR_TFVAR_MAP = {
    "charms": {
        "openstack-hypervisor": {
            "channel": "charm_channel",
            "revision": "charm_revision",
            "config": "charm_config",
        }
    }
}
DEPLOY_SUNBEAM_MACHINE_TFVAR_MAP = {
    "charms": {
        "sunbeam-machine": {
            "channel": "charm_channel",
            "revision": "charm_revision",
            "config": "charm_config",
        }
    }
}


MANIFEST_ATTRIBUTES_TFVAR_MAP = {
    "sunbeam-machine-plan": DEPLOY_SUNBEAM_MACHINE_TFVAR_MAP,
    "microk8s-plan": DEPLOY_MICROK8S_TFVAR_MAP,
    "microceph-plan": DEPLOY_MICROCEPH_TFVAR_MAP,
    "openstack-plan": DEPLOY_OPENSTACK_TFVAR_MAP,
    "hypervisor-plan": DEPLOY_OPENSTACK_HYPERVISOR_TFVAR_MAP,
}
