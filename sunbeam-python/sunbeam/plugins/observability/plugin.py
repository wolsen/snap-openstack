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

"""Observability plugin.

Plugin to deploy and manage observability, powered by COS Lite.
"""

import logging
from pathlib import Path
from typing import Optional

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.microk8s import (
    CREDENTIAL_SUFFIX,
    MICROK8S_CLOUD,
    MICROK8S_DEFAULT_STORAGECLASS,
)
from sunbeam.commands.openstack import OPENSTACK_MODEL, PatchLoadBalancerServicesStep
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    read_config,
    run_plan,
    update_config,
    update_status_background,
)
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
    JujuHelper,
    JujuWaitException,
    TimeoutException,
    run_sync,
)
from sunbeam.jobs.manifest import AddManifestStep, Manifest
from sunbeam.plugins.interface.v1.base import EnableDisablePlugin, PluginRequirement
from sunbeam.plugins.interface.v1.openstack import (
    OPENSTACK_TERRAFORM_PLAN,
    OPENSTACK_TERRAFORM_VARS,
)

LOG = logging.getLogger(__name__)
console = Console()

OBSERVABILITY_MODEL = "observability"
OBSERVABILITY_DEPLOY_TIMEOUT = 1200  # 20 minutes
CONTROLLER_MODEL = CONTROLLER_MODEL.split("/")[-1]
COS_TFPLAN = "cos-plan"
GRAFANA_AGENT_TFPLAN = "grafana-agent-plan"
GRAFANA_AGENT_K8S_TFPLAN = "grafana-agent-k8s-plan"
COS_CONFIG_KEY = "TerraformVarsPluginObservabilityPlanCos"
GRAFANA_AGENT_CONFIG_KEY = "TerraformVarsPluginObservabilityPlanGrafanaAgent"
GRAFANA_AGENT_K8S_CONFIG_KEY = "TerraformVarsPluginObservabilityPlanGrafanaAgentK8s"

COS_CHANNEL = "1.0/candidate"
GRAFANA_AGENT_CHANNEL = "latest/edge"
GRAFANA_AGENT_K8S_CHANNEL = "latest/stable"


class FillObservabilityOffersStep(BaseStep):
    """Update terraform plan to fill observability offers."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        tfhelper_cos: TerraformHelper,
        jhelper: JujuHelper,
    ) -> None:
        super().__init__(
            "Fill Observability Offers",
            "Fill Observability Offers in Openstack",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.tfhelper_cos = tfhelper_cos
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration"""
        config_key = OPENSTACK_TERRAFORM_VARS

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        output_vars = self.tfhelper_cos.output()

        for key, value in output_vars.items():
            if key in (
                "prometheus-metrics-offer-url",
                "grafana-dashboard-offer-url",
            ):
                tfvars[key] = value
        update_config(self.client, config_key, tfvars)
        self.tfhelper.write_tfvars(tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveObservabilityIntegrationStep(BaseStep):
    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ) -> None:
        super().__init__(
            "Remove Observability Integration",
            "Remove Observability Integration in Openstack",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration"""
        config_key = OPENSTACK_TERRAFORM_VARS

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}

        tfvars.pop("prometheus-metrics-offer-url", None)
        tfvars.pop("grafana-dashboard-offer-url", None)
        update_config(self.client, config_key, tfvars)
        self.tfhelper.write_tfvars(tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DeployObservabilityStackStep(BaseStep, JujuStepHelper):
    """Deploy Observability Stack using Terraform"""

    _CONFIG = COS_CONFIG_KEY

    def __init__(
        self,
        plugin: "ObservabilityPlugin",
        jhelper: JujuHelper,
    ):
        super().__init__("Deploy Observability Stack", "Deploying Observability Stack")
        self.plugin = plugin
        self.jhelper = jhelper
        self.manifest = self.plugin.manifest
        self.tfplan = self.plugin.tfplan_cos
        self.model = OBSERVABILITY_MODEL
        self.cloud = MICROK8S_CLOUD

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        extra_tfvars = {
            "model": self.model,
            "cloud": self.cloud,
            "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
            "config": {"workload-storage": MICROK8S_DEFAULT_STORAGECLASS},
        }

        try:
            self.update_status(status, "deploying services")
            self.manifest.update_tfvars_and_apply_tf(
                tfplan=self.tfplan,
                tfvar_config=self._CONFIG,
                override_tfvars=extra_tfvars,
            )
        except TerraformException as e:
            LOG.exception("Error deploying Observability Stack")
            return Result(ResultType.FAILED, str(e))

        apps = run_sync(self.jhelper.get_application_names(self.model))
        LOG.debug(f"Application monitored for readiness: {apps}")
        task = run_sync(update_status_background(self, apps, status))
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug("Failed to deploy Observability Stack", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        finally:
            if not task.done():
                task.cancel()

        return Result(ResultType.COMPLETED)


class DeployGrafanaAgentStep(BaseStep, JujuStepHelper):
    """Deploy Grafana Agent using Terraform"""

    _CONFIG = GRAFANA_AGENT_CONFIG_KEY

    def __init__(
        self,
        plugin: "ObservabilityPlugin",
        jhelper: JujuHelper,
    ):
        super().__init__("Deploy Grafana Agent", "Deploy Grafana Agent")
        self.plugin = plugin
        self.jhelper = jhelper
        self.manifest = self.plugin.manifest
        self.tfplan = self.plugin.tfplan_grafana_agent
        self.model = CONTROLLER_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        tfhelper_cos = self.manifest.get_tfhelper(COS_TFPLAN)
        cos_backend = tfhelper_cos.backend
        cos_backend_config = tfhelper_cos.backend_config()

        extra_tfvars = {
            "principal-application-model": self.model,
            "cos-state-backend": cos_backend,
            "cos-state-config": cos_backend_config,
            "principal-application": "openstack-hypervisor",
        }

        try:
            self.update_status(status, "deploying services")
            self.manifest.update_tfvars_and_apply_tf(
                tfplan=self.tfplan,
                tfvar_config=self._CONFIG,
                override_tfvars=extra_tfvars,
            )
        except TerraformException as e:
            LOG.exception("Error deploying grafana agent")
            return Result(ResultType.FAILED, str(e))

        app = "grafana-agent"
        LOG.debug(f"Application monitored for readiness: {app}")
        try:
            run_sync(
                self.jhelper.wait_application_ready(
                    app,
                    self.model,
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug("Failed to deploy grafana agent", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DeployGrafanaAgentK8sStep(BaseStep, JujuStepHelper):
    """Deploy Grafana Agent k8s using Terraform"""

    _CONFIG = GRAFANA_AGENT_K8S_CONFIG_KEY

    def __init__(
        self,
        plugin: "ObservabilityPlugin",
        jhelper: JujuHelper,
    ):
        super().__init__("Deploy Grafana Agent k8s", "Deploying Grafana Agent k8s")
        self.plugin = plugin
        self.jhelper = jhelper
        self.manifest = self.plugin.manifest
        self.tfplan = self.plugin.tfplan_grafana_agent_k8s
        self.model = OPENSTACK_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        app = "grafana-agent-k8s"
        tfhelper_cos = self.manifest.get_tfhelper(COS_TFPLAN)
        cos_backend = tfhelper_cos.backend
        cos_backend_config = tfhelper_cos.backend_config()

        extra_tfvars = {
            "cos-state-backend": cos_backend,
            "cos-state-config": cos_backend_config,
            "model": self.model,
        }

        self.update_status(status, "deploying application")
        try:
            self.manifest.update_tfvars_and_apply_tf(
                tfplan=self.tfplan,
                tfvar_config=self._CONFIG,
                override_tfvars=extra_tfvars,
            )
        except TerraformException as e:
            LOG.exception("Error deploying %s", app)
            return Result(ResultType.FAILED, str(e))

        LOG.debug("Application monitored for readiness: %s", app)
        try:
            # Note that grafana agent k8s will be blocked first if there's not
            # workload "requires" relations. We will add them later in the
            # steps.
            run_sync(
                self.jhelper.wait_application_ready(
                    app,
                    self.model,
                    accepted_status=["active", "blocked"],
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug("Failed to deploys %s", app, exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveObservabilityStackStep(BaseStep, JujuStepHelper):
    """Remove Observability Stack using Terraform"""

    def __init__(
        self,
        plugin: "ObservabilityPlugin",
        jhelper: JujuHelper,
    ):
        super().__init__("Remove Observability Stack", "Removing Observability Stack")
        self.plugin = plugin
        self.manifest = self.plugin.manifest
        self.tfplan = self.plugin.tfplan_cos
        self.jhelper = jhelper
        self.model = OBSERVABILITY_MODEL
        self.cloud = MICROK8S_CLOUD

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        tfhelper = self.manifest.get_tfhelper(self.tfplan)
        try:
            tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying Observability Stack")
            return Result(ResultType.FAILED, str(e))

        try:
            run_sync(
                self.jhelper.wait_model_gone(
                    self.model,
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.debug("Failed to destroy Observability Stack", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveGrafanaAgentStep(BaseStep, JujuStepHelper):
    """Remove Grafana Agent using Terraform"""

    def __init__(
        self,
        plugin: "ObservabilityPlugin",
        jhelper: JujuHelper,
    ):
        super().__init__("Remove Grafana Agent", "Removing Grafana Agent")
        self.plugin = plugin
        self.manifest = self.plugin.manifest
        self.tfplan = self.plugin.tfplan_grafana_agent
        self.jhelper = jhelper
        self.model = CONTROLLER_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        tfhelper = self.manifest.get_tfhelper(self.tfplan)
        try:
            tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying grafana agent")
            return Result(ResultType.FAILED, str(e))

        apps = ["grafana-agent"]
        try:
            run_sync(
                self.jhelper.wait_application_gone(
                    apps,
                    self.model,
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.debug("Failed to destroy grafana agent", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveGrafanaAgentK8sStep(BaseStep, JujuStepHelper):
    """Remove Grafana Agent k8s using Terraform"""

    def __init__(
        self,
        plugin: "ObservabilityPlugin",
        jhelper: JujuHelper,
    ):
        super().__init__("Remove Grafana Agent k8s", "Removing Grafana Agent k8s")
        self.plugin = plugin
        self.manifest = self.plugin.manifest
        self.tfplan = self.plugin.tfplan_grafana_agent_k8s
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        app = "grafana-agent-k8s"
        tfhelper = self.manifest.get_tfhelper(self.tfplan)
        try:
            tfhelper.destroy()
        except TerraformException as e:
            LOG.exception("Error destroying %s", app)
            return Result(ResultType.FAILED, str(e))

        try:
            run_sync(
                self.jhelper.wait_application_gone(
                    [app],
                    self.model,
                    timeout=OBSERVABILITY_DEPLOY_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.debug("Failed to destroy %s", app, exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class PatchCosLoadBalancerStep(PatchLoadBalancerServicesStep):
    SERVICES = ["traefik"]
    MODEL = OBSERVABILITY_MODEL


class ObservabilityPlugin(EnableDisablePlugin):
    version = Version("0.0.1")
    requires = {PluginRequirement("telemetry", optional=True)}

    def __init__(self, client: Client) -> None:
        super().__init__("observability", client)
        self.snap = Snap()
        self.tfplan_cos = COS_TFPLAN
        self.tfplan_cos_dir = "deploy-cos"
        self.tfplan_grafana_agent = GRAFANA_AGENT_TFPLAN
        self.tfplan_grafana_agent_dir = "deploy-grafana-agent"
        self.tfplan_grafana_agent_k8s = GRAFANA_AGENT_K8S_TFPLAN
        self.tfplan_grafana_agent_k8s_dir = "deploy-grafana-agent-k8s"
        self._manifest = None

    @property
    def manifest(self) -> Manifest:
        if self._manifest:
            return self._manifest

        self._manifest = Manifest.load_latest_from_clusterdb(
            self.client, include_defaults=True
        )
        return self._manifest

    def manifest_defaults(self) -> dict:
        """Manifest plugin part in dict format."""
        return {
            "charms": {
                "cos-traefik-k8s": {"channel": COS_CHANNEL},
                "alertmanager-k8s": {"channel": COS_CHANNEL},
                "grafana-k8s": {"channel": COS_CHANNEL},
                "catalogue-k8s": {"channel": COS_CHANNEL},
                "prometheus-k8s": {"channel": COS_CHANNEL},
                "loki-k8s": {"channel": COS_CHANNEL},
                "grafana-agent": {"channel": GRAFANA_AGENT_CHANNEL},
                "grafana-agent-k8s": {"channel": GRAFANA_AGENT_K8S_CHANNEL},
            },
            "terraform": {
                self.tfplan_cos: {
                    "source": Path(__file__).parent / "etc" / self.tfplan_cos_dir
                },
                self.tfplan_grafana_agent: {
                    "source": Path(__file__).parent
                    / "etc"  # noqa: W503
                    / self.tfplan_grafana_agent_dir  # noqa: W503
                },
                self.tfplan_grafana_agent_k8s: {
                    "source": Path(__file__).parent
                    / "etc"  # noqa: W503
                    / self.tfplan_grafana_agent_k8s_dir  # noqa: W503
                },
            },
        }

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan_cos: {
                "charms": {
                    "cos-traefik-k8s": {
                        "channel": "traefik-channel",
                        "revision": "traefik-revision",
                        "config": "traefik-config",
                    },
                    "alertmanager-k8s": {
                        "channel": "alertmanager-channel",
                        "revision": "alertmanager-revision",
                        "config": "alertmanager-config",
                    },
                    "grafana-k8s": {
                        "channel": "grafana-channel",
                        "revision": "grafana-revision",
                        "config": "grafana-config",
                    },
                    "catalogue-k8s": {
                        "channel": "catalogue-channel",
                        "revision": "catalogue-revision",
                        "config": "catalogue-config",
                    },
                    "prometheus-k8s": {
                        "channel": "prometheus-channel",
                        "revision": "prometheus-revision",
                        "config": "prometheus-config",
                    },
                    "loki-k8s": {
                        "channel": "loki-channel",
                        "revision": "loki-revision",
                        "config": "loki-config",
                    },
                }
            },
            self.tfplan_grafana_agent: {
                "charms": {
                    "grafana-agent": {
                        "channel": "grafana-agent-channel",
                        "revision": "grafana-agent-revision",
                        "config": "grafana-agent-config",
                    }
                }
            },
            self.tfplan_grafana_agent_k8s: {
                "charms": {
                    "grafana-agent-k8s": {
                        "channel": "grafana-agent-k8s-channel",
                        "revision": "grafana-agent-k8s-revision",
                        "config": "grafana-agent-k8s-config",
                    }
                }
            },
        }

    def run_enable_plans(self):
        data_location = self.snap.paths.user_data
        jhelper = JujuHelper(self.client, data_location)

        tfhelper_cos = self.manifest.get_tfhelper(self.tfplan_cos)
        tfhelper_openstack = self.manifest.get_tfhelper(
            f"{OPENSTACK_TERRAFORM_PLAN}-plan"
        )

        plan = []
        if self.user_manifest:
            plan.append(AddManifestStep(self.client, self.user_manifest))

        cos_plan = [
            TerraformInitStep(tfhelper_cos),
            DeployObservabilityStackStep(self, jhelper),
            PatchCosLoadBalancerStep(self.client),
            FillObservabilityOffersStep(
                self.client, tfhelper_openstack, tfhelper_cos, jhelper
            ),
        ]

        grafana_agent_plan = [
            TerraformInitStep(self.manifest.get_tfhelper(self.tfplan_grafana_agent)),
            DeployGrafanaAgentStep(self, jhelper),
        ]

        grafana_agent_k8s_plan = [
            TerraformInitStep(
                self.manifest.get_tfhelper(self.tfplan_grafana_agent_k8s)
            ),
            DeployGrafanaAgentK8sStep(self, jhelper),
        ]

        run_plan(plan, console)
        run_plan(cos_plan, console)
        run_plan(grafana_agent_plan, console)
        run_plan(grafana_agent_k8s_plan, console)

        click.echo("Observability enabled.")

    def run_disable_plans(self):
        data_location = self.snap.paths.user_data
        jhelper = JujuHelper(self.client, data_location)

        tfhelper_openstack = self.manifest.get_tfhelper(
            f"{OPENSTACK_TERRAFORM_PLAN}-plan"
        )

        cos_plan = [
            TerraformInitStep(self.manifest.get_tfhelper(self.tfplan_cos)),
            RemoveObservabilityIntegrationStep(
                self.client, tfhelper_openstack, jhelper
            ),
            RemoveObservabilityStackStep(self, jhelper),
        ]

        grafana_agent_plan = [
            TerraformInitStep(self.manifest.get_tfhelper(self.tfplan_grafana_agent)),
            RemoveGrafanaAgentStep(self, jhelper),
        ]

        grafana_agent_k8s_plan = [
            TerraformInitStep(
                self.manifest.get_tfhelper(self.tfplan_grafana_agent_k8s)
            ),
            RemoveGrafanaAgentK8sStep(self, jhelper),
        ]

        run_plan(grafana_agent_k8s_plan, console)
        run_plan(grafana_agent_plan, console)
        run_plan(cos_plan, console)
        click.echo("Observability disabled.")

    @click.command()
    def enable_plugin(self) -> None:
        """Enable Observability."""
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        """Disable  Observability."""
        super().disable_plugin()

    @click.group()
    def observability_group(self):
        """Manage Observability."""

    @click.command()
    def dashboard_url(self) -> None:
        """Retrieve COS Dashboard URL."""
        data_location = self.snap.paths.user_data
        jhelper = JujuHelper(self.client, data_location)

        with console.status("Retrieving dashboard URL from Grafana service ... "):
            # Retrieve config from juju actions
            model = OBSERVABILITY_MODEL
            app = "grafana"
            action_cmd = "get-admin-password"
            unit = run_sync(jhelper.get_leader_unit(app, model))
            if not unit:
                _message = f"Unable to get {app} leader"
                raise click.ClickException(_message)

            action_result = run_sync(jhelper.run_action(unit, model, action_cmd))

            if action_result.get("return-code", 0) > 1:
                _message = "Unable to retrieve URL from Grafana service"
                raise click.ClickException(_message)

            url = action_result.get("url")
            if url:
                console.print(url)
            else:
                _message = "No URL provided by Grafana service"
                raise click.ClickException(_message)

    def commands(self) -> dict:
        """Dict of clickgroup along with commands."""
        commands = super().commands()
        try:
            enabled = self.enabled
        except ClusterServiceUnavailableException:
            LOG.debug(
                "Failed to query for plugin status, is cloud bootstrapped ?",
                exc_info=True,
            )
            enabled = False

        if enabled:
            commands.update(
                {
                    "init": [
                        {"name": "observability", "command": self.observability_group}
                    ],
                    "init.observability": [
                        {"name": "dashboard-url", "command": self.dashboard_url}
                    ],
                }
            )
        return commands
