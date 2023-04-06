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

terraform {

  required_providers {
    juju = {
      source  = "juju/juju"
      version = ">= 0.6.0"
    }
  }

}

provider "juju" {}

# Offers should be in the openstack control plane deployment plan but
# need to be here for the moment due to
# https://github.com/juju/terraform-provider-juju/issues/190
resource "juju_offer" "rabbit_offer" {
  model            = var.openstack_model
  application_name = "rabbitmq"
  endpoint         = "amqp"
}

resource "juju_offer" "keystone_offer" {
  model            = var.openstack_model
  application_name = "keystone"
  endpoint         = "identity-credentials"
}

resource "juju_offer" "ca_offer" {
  model            = var.openstack_model
  application_name = "certificate-authority"
  endpoint         = "certificates"
}

resource "juju_application" "openstack-hypervisor" {
  name  = "openstack-hypervisor"
  trust = false
  model = var.hypervisor_model
  units = length(var.machine_ids) # need to manage the number of units

  charm {
    name    = "openstack-hypervisor"
    channel = var.charm_channel
    series  = "jammy"
  }

  config = {
    snap-channel = var.snap_channel
  }

}

resource "juju_integration" "hypervisor_amqp" {
  model = var.hypervisor_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "amqp"
  }

  application {
    offer_url = juju_offer.rabbit_offer.url
  }
}

