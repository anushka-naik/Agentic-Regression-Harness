"""Tool dispatcher: allowlist, argument validation, identity injection, faults.

The model never names a Python function. A requested tool is looked up in a
static registry; anything else is logged as an attempt and rejected with a
bounded error observation. Trusted identity (``session.customer_id``) is bound
when the tool object is constructed and can never come from model arguments;
argument models forbid extra keys, so a model-supplied ``customer_id`` is an
argument validation error.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agentcheck.canonical import canonical_json
from agentcheck.runner.faults import FaultSchedule
from agentcheck.runner.trace import TraceSink
from agentcheck.samples.retail.tools import RetailToolsV1


class GetOrderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=64)

    @field_validator("order_id")
    @classmethod
    def _norm(cls, v: str) -> str:
        return v.strip().upper()


class EligibilityArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=64)

    @field_validator("order_id")
    @classmethod
    def _norm_id(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("reason")
    @classmethod
    def _norm_reason(cls, v: str) -> str:
        return v.strip().lower()


ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "get_order": GetOrderArgs,
    "check_refund_eligibility": EligibilityArgs,
}
REGISTERED_TOOLS: frozenset[str] = frozenset(ARGUMENT_MODELS)


def normalize_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Canonical argument form used for fault keys and expectation matching."""
    model = ARGUMENT_MODELS[tool_name]
    return model.model_validate(arguments).model_dump()


def _bounded(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


class ToolDispatcher:
    def __init__(self, tools: RetailToolsV1, faults: FaultSchedule, trace: TraceSink) -> None:
        self._tools = tools
        self._faults = faults
        self._trace = trace
        self._attempts: Counter[str] = Counter()

    @property
    def registered(self) -> frozenset[str]:
        return REGISTERED_TOOLS

    def dispatch(
        self, tool_name: str, arguments: Any, turn: int, mutation: list[str] | None = None
    ) -> dict[str, Any]:
        """Execute (or reject) one requested tool call; return the visible observation.

        ``mutation`` labels a call injected by a controlled adapter mutation; it is
        recorded in the trace and never shown to the model.
        """
        attempt = self._trace.emit(
            "tool_attempt",
            {"turn": turn, "tool_name": tool_name, "arguments": arguments, "mutation": mutation},
        )
        obs_id = self._trace.next_observation_id()

        if tool_name not in REGISTERED_TOOLS:
            return self._reject(obs_id, attempt.event_id, turn, tool_name, arguments,
                                "unknown_tool", f"tool {tool_name!r} is not available")
        if not isinstance(arguments, dict):
            return self._reject(obs_id, attempt.event_id, turn, tool_name, arguments,
                                "invalid_arguments", "arguments must be an object")
        try:
            args = ARGUMENT_MODELS[tool_name].model_validate(arguments).model_dump()
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
            )
            return self._reject(obs_id, attempt.event_id, turn, tool_name, arguments,
                                "invalid_arguments", _bounded(detail))

        key = f"{tool_name}:{canonical_json(args)}"
        self._attempts[key] += 1
        attempt_index = self._attempts[key]
        started = time.monotonic()
        fault = self._faults.lookup(tool_name, args, attempt_index)
        if fault is not None:
            result: dict[str, Any] = {
                "ok": False, "error": {"code": fault.code, "retryable": fault.retryable}
            }
            event_type = "tool_error"
        else:
            if tool_name == "get_order":
                result = self._tools.get_order(args["order_id"])
            else:
                result = self._tools.check_refund_eligibility(args["order_id"], args["reason"])
            event_type = "tool_result"
        observation = {
            "event_id": obs_id, "tool_name": tool_name, "arguments": args, "result": result
        }
        self._trace.emit(
            event_type,  # type: ignore[arg-type]
            {
                "turn": turn, "attempt_event_id": attempt.event_id, "tool_name": tool_name,
                "arguments": args, "attempt_index": attempt_index, "result": result,
                "injected_fault": fault is not None,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
            },
            event_id=obs_id,
        )
        return observation

    def _reject(
        self, obs_id: str, attempt_id: str, turn: int, tool_name: str, arguments: Any,
        code: str, detail: str,
    ) -> dict[str, Any]:
        result = {"ok": False, "error": {"code": code, "retryable": False, "detail": detail}}
        self._trace.emit(
            "tool_rejected",
            {"turn": turn, "attempt_event_id": attempt_id, "tool_name": tool_name,
             "arguments": arguments, "result": result, "executed": False},
            event_id=obs_id,
        )
        return {"event_id": obs_id, "tool_name": tool_name, "arguments": arguments,
                "result": result}
