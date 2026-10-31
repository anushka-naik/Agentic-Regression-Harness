"""Scripted multi-turn user. No LLM user simulator.

The actor sees only messages. The response predicates that decide whether the
next scripted message is supplied stay private to this object.
"""

from __future__ import annotations

from agentcheck.schemas.cases import DialogueStep
from agentcheck.schemas.protocol import FinalResponse


class ScriptedUser:
    def __init__(self, steps: list[DialogueStep]) -> None:
        self._steps = steps
        self._index = 0
        self.completed = False
        self.failure: str | None = None

    @property
    def turns_supplied(self) -> int:
        return self._index + 1 if self._steps else 0

    def first_message(self) -> str:
        return self._steps[0].user

    def next_message(self, response: FinalResponse) -> str | None:
        step = self._steps[self._index]
        if step.after_response == "stop":
            self.completed = True
            return None
        if not step.after_response.matches(response):
            self.failure = (
                f"turn {self._index + 1}: expected outcome={step.after_response.outcome}"
                f"{'/' + step.after_response.requested_field if step.after_response.requested_field else ''}"
                f", got outcome={response.outcome}"
                f"{'/' + response.requested_field if response.requested_field else ''}"
            )
            return None
        self._index += 1
        return self._steps[self._index].user
