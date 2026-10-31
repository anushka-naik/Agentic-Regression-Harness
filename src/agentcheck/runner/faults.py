"""Deterministic fault injection.

A fault is keyed by (tool name, canonical normalised arguments, attempt index).
Attempt indices count calls for that pair within one case execution; a fresh
schedule is built for every side and repeat, so a scripted "first attempt times
out" happens identically for baseline and candidate. Faults are virtual: they
return immediately, so offline tests never sleep.
"""

from __future__ import annotations

from typing import Any

from agentcheck.canonical import canonical_json
from agentcheck.schemas.cases import FaultError, FaultPlanEntry


class FaultSchedule:
    def __init__(self, plan: list[FaultPlanEntry]) -> None:
        from agentcheck.tools.dispatcher import normalize_arguments

        self._faults: dict[tuple[str, str, int], FaultError] = {}
        for entry in plan:
            args = canonical_json(normalize_arguments(entry.tool_name, entry.arguments))
            for attempt in entry.attempts:
                self._faults[(entry.tool_name, args, attempt.index)] = attempt.error

    def lookup(self, tool_name: str, args: dict[str, Any], attempt_index: int) -> FaultError | None:
        return self._faults.get((tool_name, canonical_json(args), attempt_index))
