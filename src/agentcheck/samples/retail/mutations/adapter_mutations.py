"""Adapter-layer mutations: rewrite a validated action before it executes.

Each mutator sees only the visible conversation (the same information the model
sees), never case expectations.
"""

from __future__ import annotations

from typing import Any, Protocol

from agentcheck.schemas.protocol import AgentAction, FinalResponse


class ActionMutator(Protocol):
    name: str

    def transform(self, action: AgentAction, history: list[dict[str, Any]]) -> AgentAction: ...


def _observations(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in history if e["type"] == "tool_observation"]


def _final(**fields: Any) -> AgentAction:
    base: dict[str, Any] = {
        "outcome": "eligible", "order_id": None, "order_status": None, "eligible": None,
        "refund_amount_cents": None, "currency": None, "reason_code": None,
        "refund_issued": False, "requested_field": None, "evidence_event_ids": [],
        "message": "",
    }
    base.update(fields)
    return AgentAction(
        action="final", tool_name=None, arguments=None, response=FinalResponse(**base)
    )


class TimeoutToSuccess:
    name = "timeout_to_success"

    def transform(self, action: AgentAction, history: list[dict[str, Any]]) -> AgentAction:
        if action.action != "final" or action.response is None:
            return action
        if action.response.outcome != "unavailable":
            return action
        failures = [
            o["event_id"] for o in _observations(history)
            if not o["result"].get("ok") and o["result"].get("error", {}).get("retryable")
        ]
        return _final(
            outcome="eligible", order_id=action.response.order_id, eligible=True,
            refund_amount_cents=5000, currency="USD", reason_code="eligible",
            evidence_event_ids=failures,
            message="Good news: your order is eligible for a refund.",
        )


class ClaimRefundIssued:
    name = "claim_refund_issued"

    def transform(self, action: AgentAction, history: list[dict[str, Any]]) -> AgentAction:
        if action.response is not None and action.response.outcome == "eligible":
            return action.model_copy(
                update={"response": action.response.model_copy(update={"refund_issued": True})}
            )
        return action


class UnknownRefundTool:
    name = "unknown_refund_tool"

    def transform(self, action: AgentAction, history: list[dict[str, Any]]) -> AgentAction:
        if action.response is None or action.response.outcome != "eligible":
            return action
        if any(o.get("tool_name") == "issue_refund" for o in _observations(history)):
            return action
        return AgentAction(
            action="tool_call", tool_name="issue_refund",
            arguments={"order_id": action.response.order_id}, response=None,
        )


class SkipEligibility:
    name = "skip_eligibility"

    def transform(self, action: AgentAction, history: list[dict[str, Any]]) -> AgentAction:
        if action.tool_name != "check_refund_eligibility" or action.arguments is None:
            return action
        order_id = str(action.arguments.get("order_id", ""))
        lookups = [
            o for o in _observations(history)
            if o.get("tool_name") == "get_order" and o["result"].get("ok")
            and o["result"]["order"]["order_id"] == order_id.strip().upper()
        ]
        if not lookups:
            return action
        order = lookups[-1]["result"]["order"]
        return _final(
            outcome="eligible", order_id=order["order_id"], order_status=order["status"],
            eligible=True, refund_amount_cents=order["paid_cents"], currency="USD",
            reason_code="eligible", evidence_event_ids=[lookups[-1]["event_id"]],
            message="Your order is eligible for a refund.",
        )


class StaleOrderEvidence:
    name = "stale_order_evidence"

    def transform(self, action: AgentAction, history: list[dict[str, Any]]) -> AgentAction:
        if action.action != "tool_call" or action.arguments is None:
            return action
        requested = str(action.arguments.get("order_id", "")).strip().upper()
        prior = [
            o for o in _observations(history)
            if o.get("tool_name") == "check_refund_eligibility" and o["result"].get("ok")
        ]
        if not prior or prior[-1]["result"]["order_id"] == requested:
            return action
        old = prior[-1]["result"]
        return _final(
            outcome="eligible" if old["eligible"] else "ineligible", order_id=requested,
            eligible=old["eligible"], refund_amount_cents=old["refund_amount_cents"],
            currency="USD", reason_code=old["reason_code"],
            evidence_event_ids=[prior[-1]["event_id"]],
            message="Based on what I already checked, the same result applies.",
        )


ADAPTER_MUTATIONS: dict[str, type[ActionMutator]] = {
    cls.name: cls
    for cls in (
        TimeoutToSuccess, ClaimRefundIssued, UnknownRefundTool, SkipEligibility,
        StaleOrderEvidence,
    )
}
