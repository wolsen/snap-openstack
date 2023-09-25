# DNS service

This plugin provides a DNS service for Sunbeam. It based on [Designate](https://docs.openstack.org/designate/latest/), a DNS service for OpenStack and Bind.

## Installation

To enable the DNS service, you need an already bootstraped Sunbeam instance. Then, you can install the plugin with:

```bash
sunbeam enable dns --nameservers="<ns records>"
```

## Contents

This plugin will install the following services:
- Bind: a DNS server
- Designate: a DNS service for OpenStack
- MySQL Router for Designate
- MySQL Instance in the case of a multi-mysql installation (for large deployments)

## Configuration

The NS records you pass to the `--nameservers` must be a fully qualified domain name ending with a dot.
It must redirect towards the IP address of the bind instance. See [#commands](#commands) to retrieve the address of the bind instance.

## Commands

To retrieve the plugin commands, run:

```bash
sunbeam dns --help
```

The plugin provides address command, that will return the address of the bind instance. To retrieve the address, run:

```bash
sunbeam dns address
```

## Removal

To remove the plugin, run:

```bash
sunbeam disable dns
```
