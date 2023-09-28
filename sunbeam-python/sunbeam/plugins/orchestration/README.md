# Orchestration service

This plugin provides Orchestration service for Sunbeam. It based on [Heat](https://docs.openstack.org/heat/latest/), an Orchestration service for OpenStack.

## Installation

To enable the Orchestration service, you need an already bootstraped Sunbeam instance. Then, you can install the plugin with:

```bash
sunbeam enable orchestration
```

## Contents

This plugin will install the following services:
- Heat: Orchestration service for OpenStack [charm](https://opendev.org/openstack/charm-heat-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/heat-consolidated)
- Heat CFN: Another instance of Heat with [heat-api-cfn](https://docs.openstack.org/heat/latest/man/heat-api-cfn.html) service
- MySQL Router for Heat [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.


## Removal

To remove the plugin, run:

```bash
sunbeam disable orchestration
```
