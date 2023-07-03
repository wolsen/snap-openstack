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

from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.clusterd.client import Client

LOG = logging.getLogger(__name__)


class SetBootstrapped(BaseStep):
    """Post Deployment step to configure MySQL."""

    def __init__(self):
        super().__init__("Mark bootstrapped", "Mark deployment bootstrapped")
        self.client = Client()

    def run(self, status: Optional[Status] = None) -> Result:
        LOG.warning("Setting deployment as bootstrapped")
        self.client.cluster.set_sunbeam_bootstrapped()
        return Result(ResultType.COMPLETED)
