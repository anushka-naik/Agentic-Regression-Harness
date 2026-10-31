"""Structured action protocol spoken between the harness and the model.

This is *not* native function calling: the model returns one JSON action per
request, constrained to this shape by Ollama's ``format`` field and then
validated again in Python. The schema enforces shape, never the right answer:
``refund_issued`` is a plain boolean so a wrong ``true`` stays detectable, and
``tool_name`` is a free string so attempts to call unregistered tools stay
observable.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Outcome = Literal[
    "order_status", "eligible", "ineligible", "needs_info", "not_found", "unavailable", "unsupported"
]
RequestedField = Literal["order_id", "reason"]


class FinalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    order_id: str | None
    order_status: str | None
    eligible: bool | None
    refund_amount_cents: int | None = Field(ge=0)
    currency: str | None
    reason_code: str | None
    refund_issued: bool
    requested_field: RequestedField | None
    evidence_event_ids: list[str]
    message: str = Field(max_length=2000)


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["tool_call", "final"]
    tool_name: str | None
    arguments: dict[str, Any] | None
    response: FinalResponse | None

    @model_validator(mode="after")
    def _shape(self) -> AgentAction:
        if self.action == "tool_call":
            if not self.tool_name or self.arguments is None:
                raise ValueError("tool_call requires tool_name and arguments")
            if self.response is not None:
                raise ValueError("tool_call must have response=null")
        else:
            if self.response is None:
                raise ValueError("final requires response")
            if self.tool_name is not None or self.arguments is not None:
                raise ValueError("final must have tool_name=null and arguments=null")
        return self


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            return _inline_refs(copy.deepcopy(defs[name]), defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k not in ("$defs", "title")}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def action_json_schema() -> dict[str, Any]:
    """JSON schema sent to Ollama's ``format`` field, with ``$ref`` inlined.

    The two action shapes are expressed as an ``anyOf`` so constrained decoding
    also enforces the per-action null rules that :class:`AgentAction`'s validator
    checks. This constrains *shape* only: tool names stay free strings and every
    response field keeps its full type, so wrong answers remain expressible and
    detectable.
    """
    final = FinalResponse.model_json_schema()
    response: dict[str, Any] = _inline_refs(final, final.get("$defs", {}))
    return {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "action": {"const": "tool_call"},
                    "tool_name": {"type": "string"},
                    "arguments": {"type": "object"},
                    "response": {"type": "null"},
                },
                "required": ["action", "tool_name", "arguments", "response"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "final"},
                    "tool_name": {"type": "null"},
                    "arguments": {"type": "null"},
                    "response": response,
                },
                "required": ["action", "tool_name", "arguments", "response"],
                "additionalProperties": False,
            },
        ]
    }
