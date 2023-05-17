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

import logging
from typing import Optional

from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.microceph import APPLICATION as MICROCEPH_APPLICATION
from sunbeam.commands.microk8s import (
    CREDENTIAL_SUFFIX,
    MICROK8S_CLOUD,
    MICROK8S_DEFAULT_STORAGECLASS,
)
from sunbeam.commands.terraform import TerraformException, TerraformHelper
from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
    JujuHelper,
    JujuWaitException,
    TimeoutException,
    run_sync,
)

LOG = logging.getLogger(__name__)
OPENSTACK_MODEL = "openstack"
OPENSTACK_DEPLOY_TIMEOUT = 2400  # 30 minutes


class DeployControlPlaneStep(BaseStep, JujuStepHelper):
    """Deploy OpenStack using Terraform cloud"""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Deploying OpenStack Control Plane",
            "Deploying OpenStack Control Plane to Kubernetes",
        )
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL
        self.cloud = MICROK8S_CLOUD
        self.client = Client()

    def get_storage_tfvars(self) -> dict:
        """Create terraform variables related to storage."""
        tfvars = {}
        storage_nodes = self.client.cluster.list_nodes_by_role("storage")
        if storage_nodes:
            tfvars["enable_ceph"] = True
            tfvars["ceph_offer_url"] = f"{CONTROLLER_MODEL}.{MICROCEPH_APPLICATION}"
        else:
            tfvars["enable_ceph"] = False

        return tfvars

    def run(
        self, status: Optional[Status] = None, console: Optional[Console] = None
    ) -> Result:
        """Execute configuration using terraform."""
        # TODO(jamespage):
        # This needs to evolve to add support for things like:
        # - Enabling HA
        # - Enabling/disabling specific services
        # - Switch channels for the charmed operators
        tfvars = {
            "model": self.model,
            # Make these channel options configurable by the user
            "openstack_channel": "yoga/edge",
            "ovn_channel": "22.03/edge",
            "cloud": self.cloud,
            "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
            "config": {"workload-storage": MICROK8S_DEFAULT_STORAGECLASS},
        }
        tfvars.update(self.get_storage_tfvars())

        self.tfhelper.write_tfvars(tfvars)
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error configuring cloud")
            return Result(ResultType.FAILED, str(e))

        try:
            # Remove cinder-ceph from apps to wait on if ceph is not enabled
            apps = run_sync(self.jhelper.get_application_names(self.model))
            if not tfvars.get("enable_ceph") and "cinder-ceph" in apps:
                apps.remove("cinder-ceph")

            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=OPENSTACK_DEPLOY_TIMEOUT,
                )
            )
        except JujuWaitException as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        except TimeoutException as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
