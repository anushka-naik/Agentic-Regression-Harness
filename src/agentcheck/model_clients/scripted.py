"""Scripted model clients for fixture mode and tests. No LLM involved.

* :class:`ScriptedModelClient` replays a fixed list of raw responses. It is used
  for protocol tests (malformed JSON, repairs, unknown tools).
* :class:`ReferencePolicyClient` is a small, transparent rule-based agent. It
  reads only the visible conversation (user messages and tool observations,
  exactly what a real model receives) and emits protocol actions. It never sees
  case ids or expectations. Running the whole dataset through it in fixture mode
  shows that every expectation is satisfiable by honest tool use, and gives the
  mutation suite a deterministic, variance-free control trajectory.

Results from these clients are always labelled ``evaluation_mode=fixture``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from agentcheck.errors import InfrastructureError
from agentcheck.model_clients.base import ModelConfig, ModelResponse


class ScriptedModelClient:
    provider = "scripted"

    def __init__(self, responses: Sequence[str | Callable[[list[dict[str, object]]], str]]) -> None:
        self._responses = list(responses)
        self.requests: list[list[dict[str, object]]] = []

    async def next_action(
        self, messages: list[dict[str, object]], schema: dict[str, object], config: ModelConfig
    ) -> ModelResponse:
        self.requests.append(messages)
        if not self._responses:
            raise InfrastructureError("scripted model fixture exhausted")
        item = self._responses.pop(0)
        content = item(messages) if callable(item) else item
        return ModelResponse(content=content, latency_ms=0.0)


ORDER_ID = re.compile(r"\b[oO]-\d{4}\b")
REASON_PHRASES: dict[str, tuple[str, ...]] = {
    "damaged": ("arrived damaged", "damaged", "arrived broken", "broken on arrival"),
    "wrong_item": ("wrong item", "different item", "not what i ordered"),
    "changed_mind": ("changed my mind", "no longer want", "don't want it anymore"),
}
AMBIGUOUS_REASON = re.compile(
    r"problem with|arrived late|not happy|issue with it|something wrong|didn't like"
)
NEGATION = re.compile(r"(not|wasn't|isn't|never|n't)\s+(really\s+)?$")
ISSUE_REFUND = re.compile(r"\b(issue|process|send)\s+(my|the|a)\s+refund\b")
UNSUPPORTED = re.compile(
    r"\bcancel\s+(my|the|this|order|o-)|\bchange\s+(the\s+|my\s+)?(shipping\s+)?address"
    r"|\bstore credit\b|\bexpedite\b|\bexchange\s+(it|this|my)\b|\bupgrade\s+(the\s+)?shipping\b"
)
REFUND = re.compile(r"\brefund|\breturn\b|\beligib|money back")
STATUS = re.compile(r"\bstatus\b|\bwhere is\b|\btrack|\bshipped yet\b|\bhas it shipped\b")


def _intent(text: str) -> str | None:
    lowered = text.lower()
    if ISSUE_REFUND.search(lowered):
        return "issue_refund"
    if UNSUPPORTED.search(lowered):
        return "unsupported"
    if STATUS.search(lowered):
        return "status"
    if REFUND.search(lowered):
        return "refund"
    return None


def _reason(text: str) -> str | bool | None:
    """Return a supported reason, ``None`` for an ambiguous one, ``False`` if absent."""
    lowered = text.lower()
    best: tuple[int, str] | None = None
    for code, phrases in REASON_PHRASES.items():
        for phrase in phrases:
            for match in re.finditer(re.escape(phrase), lowered):
                if NEGATION.search(lowered[: match.start()]):
                    continue
                if best is None or match.start() > best[0]:
                    best = (match.start(), code)
    if best is not None:
        return best[1]
    if AMBIGUOUS_REASON.search(lowered):
        return None
    return False


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


class ReferencePolicyClient:
    provider = "scripted"

    async def next_action(
        self, messages: list[dict[str, object]], schema: dict[str, object], config: ModelConfig
    ) -> ModelResponse:
        envelope = json.loads(str(messages[-1]["content"]))
        action = decide(envelope["conversation"])
        return ModelResponse(content=json.dumps(action), latency_ms=0.0)


def _final(outcome: str, message: str, **fields: Any) -> dict[str, Any]:
    response: dict[str, Any] = {
        "outcome": outcome, "order_id": None, "order_status": None, "eligible": None,
        "refund_amount_cents": None, "currency": None, "reason_code": None,
        "refund_issued": False, "requested_field": None, "evidence_event_ids": [],
        "message": message,
    }
    response.update(fields)
    return {"action": "final", "tool_name": None, "arguments": None, "response": response}


def _call(tool: str, **arguments: str) -> dict[str, Any]:
    return {"action": "tool_call", "tool_name": tool, "arguments": arguments, "response": None}


def decide(conversation: list[dict[str, Any]]) -> dict[str, Any]:
    users = [e["text"] for e in conversation if e["type"] == "user_message"]
    observations = [e for e in conversation if e["type"] == "tool_observation"]

    intent: str | None = None
    order_id: str | None = None
    reason: str | None = None
    for text in users:
        intent = _intent(text) or intent
        ids = ORDER_ID.findall(text)
        if ids:
            order_id = ids[-1].upper()
        found = _reason(text)
        if found is not False:
            reason = found if isinstance(found, str) else None

    def resolve(tool: str, args: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
        matching = [
            o for o in observations if o["tool_name"] == tool and o["arguments"] == args
        ]
        if not matching:
            return "call", []
        last = matching[-1]["result"]
        if last.get("ok"):
            return "ok", matching
        if last["error"].get("retryable"):
            return ("unavailable" if len(matching) >= 2 else "call"), matching
        return "error", matching

    if intent == "unsupported":
        return _final(
            "unsupported",
            "I'm sorry, I can't do that. I can look up an order or check refund eligibility, "
            "but I can't cancel orders, change addresses, issue credits, or modify orders.",
        )
    if order_id is None:
        return _final(
            "needs_info", "Could you share your order ID (for example O-1234)?",
            requested_field="order_id",
        )

    lookup_state, lookups = resolve("get_order", {"order_id": order_id})
    if lookup_state == "call":
        return _call("get_order", order_id=order_id)
    if lookup_state == "unavailable":
        return _final(
            "unavailable",
            "The order system is temporarily unavailable, so I can't check this right now. "
            "Please try again later.",
            order_id=order_id, evidence_event_ids=[o["event_id"] for o in lookups[-2:]],
        )
    if lookup_state == "error":
        return _final(
            "not_found", f"I couldn't find order {order_id} on your account.",
            order_id=order_id, reason_code="not_found",
            evidence_event_ids=[lookups[-1]["event_id"]],
        )
    lookup = lookups[-1]
    order = lookup["result"]["order"]

    if intent == "status" or intent is None:
        return _final(
            "order_status", f"Order {order_id} is currently {order['status']}.",
            order_id=order_id, order_status=order["status"],
            evidence_event_ids=[lookup["event_id"]],
        )

    if reason is None:
        return _final(
            "needs_info",
            "What's the reason for the return: damaged, wrong item, or changed your mind?",
            order_id=order_id, requested_field="reason",
        )

    elig_state, checks = resolve(
        "check_refund_eligibility", {"order_id": order_id, "reason": reason}
    )
    if elig_state == "call":
        return _call("check_refund_eligibility", order_id=order_id, reason=reason)
    if elig_state == "unavailable":
        return _final(
            "unavailable",
            "The refund eligibility service is temporarily unavailable, so I can't confirm "
            "eligibility right now.",
            order_id=order_id, order_status=order["status"],
            evidence_event_ids=[lookup["event_id"]] + [o["event_id"] for o in checks[-2:]],
        )
    if elig_state == "error":
        return _final(
            "not_found", f"I couldn't find order {order_id} on your account.",
            order_id=order_id, reason_code="not_found",
            evidence_event_ids=[checks[-1]["event_id"]],
        )
    result = checks[-1]["result"]
    limitation = " I can check eligibility, but I can't issue refunds." if intent == "issue_refund" else ""
    if result["eligible"]:
        message = (
            f"Order {order_id} is eligible for a refund of "
            f"{_dollars(result['refund_amount_cents'])}.{limitation}"
        )
    else:
        message = (
            f"Order {order_id} is not eligible for a refund "
            f"({result['reason_code'].replace('_', ' ')}).{limitation}"
        )
    return _final(
        "eligible" if result["eligible"] else "ineligible", message,
        order_id=order_id, order_status=order["status"], eligible=result["eligible"],
        refund_amount_cents=result["refund_amount_cents"], currency=result["currency"],
        reason_code=result["reason_code"],
        evidence_event_ids=[lookup["event_id"], checks[-1]["event_id"]],
    )
