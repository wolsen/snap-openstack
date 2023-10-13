# Container Infra service

This plugin provides Container Infra service for Sunbeam. It's based on [Magnum](https://docs.openstack.org/magnum/latest/), a Container Infra service for OpenStack.

## Installation

To enable the Container Infra service, you need an already bootstraped Sunbeam instance and the following plugins: Secrets, Orchestration, Loadbalancer (optional). Then, you can install the plugin with:

```bash
sunbeam enable container-infra
```

## Configure

The Container Infra service needs a compatible image to instanciate kubernetes cluster. The correct image can be setup automatically with:

```bash
sunbeam configure container-infra
```

The list of compatible images can be found [here](https://docs.openstack.org/magnum/latest/user/index.html#supported-versions).

## Contents

This plugin will install the following services:
- Magnum: Container Infra service for OpenStack [charm](https://opendev.org/openstack/charm-magnum-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/magnum-consolidated)
- MySQL Router for Magnum [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.

## Removal

To remove the plugin, run:

```bash
sunbeam disable container-infra
```
