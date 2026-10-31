"""Northstar Demo Store tools: the implementation *under test*.

Two read-only tools. Ownership, the refund window and amounts are decided here
and, independently, in ``agentcheck.evaluators.retail_oracle``. The two
modules must never share a decision function; that independence is what lets
the scorer catch a buggy tool implementation.

Controlled mutations subclass :class:`RetailToolsV1` and override one hook.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from agentcheck import POLICY_VERSION
from agentcheck.schemas.domain import Order, World

REFUND_WINDOW = timedelta(days=30)
REASONS = frozenset({"damaged", "wrong_item", "changed_mind"})
PUBLIC_ORDER_FIELDS = (
    "order_id", "status", "delivered_at", "paid_cents", "currency", "final_sale",
    "product_name", "public_note",
)


def _not_found() -> dict[str, Any]:
    return {"ok": False, "error": {"code": "not_found", "retryable": False}}


def _to_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError(f"naive timestamp {value!r}")
    return parsed


class RetailToolsV1:
    name = "retail_v1"

    def __init__(self, world: World, customer_id: str, now: datetime) -> None:
        self._orders = {order.order_id: order for order in world.orders}
        self._customer_id = customer_id
        self._now = now

    def _lookup(self, order_id: str) -> Order | None:
        order = self._orders.get(order_id)
        if order is None or order.customer_id != self._customer_id:
            return None
        return order

    def _public_view(self, order: Order) -> dict[str, Any]:
        record = order.model_dump()
        return {key: record[key] for key in PUBLIC_ORDER_FIELDS}

    def _within_window(self, age: timedelta) -> bool:
        return timedelta(0) <= age <= REFUND_WINDOW

    def _refund_amount(self, order: Order) -> int:
        return order.paid_cents

    def get_order(self, order_id: str) -> dict[str, Any]:
        order = self._lookup(order_id)
        if order is None:
            return _not_found()
        return {"ok": True, "order": self._public_view(order)}

    def check_refund_eligibility(self, order_id: str, reason: str) -> dict[str, Any]:
        order = self._lookup(order_id)
        if order is None:
            return _not_found()
        if reason not in REASONS:
            return {"ok": False, "error": {"code": "invalid_reason", "retryable": False}}

        code = "eligible"
        if order.status == "refunded":
            code = "already_refunded"
        elif order.status == "cancelled":
            code = "cancelled"
        elif order.status != "delivered" or order.delivered_at is None:
            code = "not_delivered"
        elif order.final_sale:
            code = "final_sale"
        elif not self._within_window(self._now - _to_utc(order.delivered_at)):
            code = "outside_window"

        eligible = code == "eligible"
        return {
            "ok": True,
            "order_id": order.order_id,
            "eligible": eligible,
            "reason_code": code,
            "refund_amount_cents": self._refund_amount(order) if eligible else 0,
            "currency": "USD",
            "policy_version": POLICY_VERSION,
        }
