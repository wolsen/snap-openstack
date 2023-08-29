# Terraform manifest for deployment of Monitoring stack
#
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

variable "model" {
  description = "Name of Juju model to use for deployment"
  default     = "cos"
}

variable "controller-model" {
  description = "Name of the sunbeam controller model"
}

variable "openstack-hypervisor-name" {
  description = "Name of the deployed openstack-hypervisor application"
  default = "openstack-hypervisor"
}

variable "cloud" {
  description = "Name of K8S cloud to use for deployment"
  default     = "microk8s"
}

# https://github.com/juju/terraform-provider-juju/issues/147
variable "credential" {
  description = "Name of credential to use for deployment"
  default     = ""
}

variable "config" {
  description = "Set configuration on model"
  default     = {}
}

variable "cos-channel" {
  description = "Operator channel for COS Lite deployment"
  default     = "1.0/stable"
}

variable "grafana-agent-channel" {
  description = "Channel to use when deploying grafana agent machine charm"
  default = "latest/candidate"
}

variable "ingress-scale" {
  description = "Scale of ingress deployment"
  default     = 1
}

variable "alertmanager-scale" {
  description = "Scale of alertmanagement deployment"
  default     = 1
}

variable "prometheus-scale" {
  description = "Scale of prometheus deployment"
  default     = 1
}

variable "grafana-scale" {
  description = "Scale of grafana deployment"
  default     = 1
}

variable "catalogue-scale" {
  description = "Scale of catalogue deployment"
  default     = 1
}

variable "loki-scale" {
  description = "Scale of loki deployment"
  default     = 1
}
