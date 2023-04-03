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

from rich.status import Status

from sunbeam.commands.juju import (
    JujuStepHelper,
)
from sunbeam.commands.microk8s import (
    CREDENTIAL_SUFFIX,
    MICROK8S_CLOUD,
    MICROK8S_DEFAULT_STORAGECLASS,
)
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
)
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
)
from sunbeam.jobs.juju import (
    JujuHelper,
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

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        # TODO(jamespage):
        # This needs to evolve to add support for things like:
        # - Enabling HA
        # - Enabling/disabling specific services
        # - Switch channels for the charmed operators
        self.tfhelper.write_tfvars(
            {
                "model": self.model,
                "cloud": self.cloud,
                "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
                "config": {"workload-storage": MICROK8S_DEFAULT_STORAGECLASS},
            }
        )
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error configuring cloud")
            return Result(ResultType.FAILED, str(e))

        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    timeout=OPENSTACK_DEPLOY_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
