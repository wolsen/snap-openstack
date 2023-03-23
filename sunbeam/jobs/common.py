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

import enum
import logging
from typing import Optional

import click
from rich.console import Console
from rich.status import Status

LOG = logging.getLogger(__name__)


class Role(enum.Enum):
    """The role that the current node will play

    This determines if the role will be a control plane node, a Compute node,
    or a Converged node. The role will help determine which particular services
    need to be configured and installed on the system.
    """

    CONTROL = 1
    COMPUTE = 2
    CONVERGED = 3

    def is_control_node(self) -> bool:
        """Returns True if the node requires control services.

        Control plane services are installed on nodes which are not designated
        for compute nodes only. This helps determine the role that the local
        node will play.

        :return: True if the node should have control-plane services,
                 False otherwise
        """
        return self != Role.COMPUTE

    def is_compute_node(self) -> bool:
        """Returns True if the node requires compute services.

        Compute services are installed on nodes which are not designated as
        control nodes only. This helps determine the services which are
        necessary to install.

        :return: True if the node should run Compute services,
                 False otherwise
        """
        return self != Role.CONTROL

    def is_converged_node(self) -> bool:
        """Returns True if the node requires control and compute services.

        Control and Compute services are installed on nodes which are
        designated as converged nodes. This helps determine the services
        which are necessary to install.

        :return: True if the node should run Control and Compute services,
                 False otherwise
        """
        return self == Role.CONVERGED


class ResultType(enum.Enum):
    COMPLETED = 0
    FAILED = 1
    SKIPPED = 2


class Result:
    """The result of running a step"""

    def __init__(self, result_type: ResultType, message: Optional[str] = ""):
        """Creates a new result

        :param result_type:
        :param message:
        """
        self.result_type = result_type
        self.message = message


class StepResult:
    """The Result of running a Step.

    The results of running contain the minimum of the ResultType to indicate
    whether running the Step was completed, failed, or skipped.
    """

    def __init__(self, result_type: ResultType = ResultType.COMPLETED, **kwargs):
        """Creates a new StepResult.

        The StepResult will contain various information regarding the result
        of running a Step. By default, a new StepResult will be created with
        result_type set to ResultType.COMPLETED.

        Additional attributes can be stored in the StepResult object by using
        the kwargs values, but the keys must be unique to the StepResult
        already. If the kwargs contains a keyword that is an attribute on the
        object then a ValueError is raised.

        :param result_type: the result of running a plan or step.
        :param kwargs: additional attributes to store in the step.
        :raises: ValueError if a key in the kwargs already exists on the
                 object.
        """
        self.result_type = result_type
        for key, value in kwargs.items():
            # Note(wolsen) this is a bit of a defensive check to make sure
            # a bit of code doesn't accidentally override a base object
            # attribute.
            if hasattr(self, key):
                raise ValueError(
                    f"{key} was specified but already exists on " f"this StepResult."
                )
            self.__setattr__(key, value)


class BaseStep:
    """A step defines a logical unit of work to be done as part of a plan.

    A step determines what needs to be done in order to perform a logical
    action as part of carrying out a plan.
    """

    def __init__(self, name: str, description: str = ""):
        """Initialise the BaseStep

        :param name: the name of the step
        """
        self.name = name
        self.description = description

    def prompt(self, console: Optional[Console] = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        pass

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return False

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status]) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        pass


def run_plan(plan: list, console: Console) -> dict:
    """Run plans sequentially.

    Runs each step of the plan, logs each step of
    the plan and returns a dictionary of results
    from each step.

    Raise ClickException in case of Result Failures.
    """
    results = {}

    for step in plan:
        LOG.debug(f"Starting step {step.name}")
        message = f"{step.description} ... "
        with console.status(f"{step.description} ... ") as status:
            if step.has_prompts():
                status.stop()
                step.prompt(console)
                status.start()

            skip_result = step.is_skip()
            if skip_result.result_type == ResultType.SKIPPED:
                results[step.__class__.__name__] = skip_result
                LOG.debug(f"Skipping step {step.name}")
                console.print(f"{message}[green]done[/green]")
                continue

            LOG.debug(f"Running step {step.name}")
            result = step.run()
            results[step.__class__.__name__] = result
            LOG.debug(
                f"Finished running step {step.name}. " f"Result: {result.result_type}"
            )

        if result.result_type == ResultType.FAILED:
            console.print(f"{message}[red]failed[/red]")
            raise click.ClickException(result.message)

        console.print(f"{message}[green]done[/green]")

    # Returns results object only when all steps have results of type
    # COMPLETED or SKIPPED.
    return results
