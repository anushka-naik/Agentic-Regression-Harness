"""Exception taxonomy. The distinction matters for scoring:

* ``InfrastructureError``: the harness or provider failed. The trial is an
  ``infrastructure_error`` and never counts as an agent pass or fail.
* ``AgentLimitExceeded``: the agent broke a budget (steps, context, time).
  This is an evaluated agent failure.
* ``ConfigurationError``: invalid input to a command (exit code 2).
"""


class AgentCheckError(Exception):
    """Base class for expected, user-facing errors."""


class ConfigurationError(AgentCheckError):
    pass


class DatasetError(AgentCheckError):
    pass


class InfrastructureError(AgentCheckError):
    pass


class ModelUnavailableError(InfrastructureError):
    pass


class IncompatibleRunsError(AgentCheckError):
    pass


class AgentLimitExceeded(AgentCheckError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
