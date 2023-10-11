# Secrets service

This plugin provides Secrets service for Sunbeam. It's based on [Barbican](https://docs.openstack.org/barbican/latest/), a Secrets service for OpenStack.

## Installation

To enable the Secrets service, you need an already bootstraped Sunbeam instance and the Vault plugin. Then, you can install the plugin with:

```bash
sunbeam enable secrets
```

## Contents

This plugin will install the following services:
- Barbican: Secrets service for OpenStack [charm](https://opendev.org/openstack/charm-barbican-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/barbican-consolidated)
- MySQL Router for Barbican [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.

## Removal

To remove the plugin, run:

```bash
sunbeam disable secrets
```
