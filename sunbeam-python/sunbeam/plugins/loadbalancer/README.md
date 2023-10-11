# Loadbalancer service

This plugin provides Loadbalancer service for Sunbeam. It's based on [Octavia](https://docs.openstack.org/octavia/latest/), load balancing solution for OpenStack.

## Installation

To enable the Loadbalancer service, you need an already bootstraped Sunbeam instance. Then, you can install the plugin with:

```bash
sunbeam enable loadbalancer
```

## Contents

This plugin will install the following services:
- Octavia: Loadbalancer service for OpenStack [charm](https://opendev.org/openstack/charm-octavia-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/octavia-consolidated)
- MySQL Router for Octavia [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.

The Octavia charm currently supports provider driver of type OVN Octavia.

## Removal

To remove the plugin, run:

```bash
sunbeam disable loadbalancer
```
