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
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
)
from sunbeam.commands.openstack import (
    OPENSTACK_MODEL,
)

LOG = logging.getLogger(__name__)
HYPERVISOR_DEPLOY_TIMEOUT = 2400  # 30 minutes


class DeployHypervisorStep(BaseStep, JujuStepHelper):
    """Deploy OpenStack Hypervisor"""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Deploying OpenStack Hypervisor",
            "Deploying OpenStack Hypervisor",
        )
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.hypervisor_model = CONTROLLER_MODEL.split("/")[-1]
        self.openstack_model = OPENSTACK_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        self.tfhelper.write_tfvars(
            {
                "placement": 0,
                "hypervisor_model": self.hypervisor_model,
                "openstack_model": self.openstack_model,
                "charm_channel": "yoga/edge",
                "snap_channel": "yoga/edge",
            }
        )
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error configuring hypervisor")
            return Result(ResultType.FAILED, str(e))

        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.hypervisor_model,
                    timeout=HYPERVISOR_DEPLOY_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
