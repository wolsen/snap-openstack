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

output "prometheus-metrics-offer-url" {
  description = "URL of the prometheus metrics endpoint offer"
  value       = juju_offer.prometheus-metrics-offer.url
}

output "prometheus-receive-remote-write-offer-url" {
  description = "URL of the prometheus receive remote write endpoint offer"
  value       = juju_offer.prometheus-receive-remote-write-offer.url
}

output "loki-logging-offer-url" {
  description = "URL of the loki logging offer"
  value       = juju_offer.loki-logging-offer.url
}

output "grafana-dashboard-offer-url" {
  description = "URL of the grafana dashboard offer"
  value       = juju_offer.grafana-dashboard-offer.url
}

output "alertmanager-karma-dashboard-offer-url" {
  description = "URL of the alertmanager karma dashboard endpoint offer"
  value       = juju_offer.alertmanager-karma-dashboard-offer.url
}
