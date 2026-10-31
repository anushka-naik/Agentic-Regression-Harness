"""Reference oracle for Northstar policy v1.

Written independently of ``samples/retail/tools.py``: it shares only immutable
types and the policy identifier. Time is compared as integer epoch seconds
rather than ``timedelta`` objects, ownership is resolved through a per-customer
index rather than a lookup-then-compare, and precedence is expressed as an
ordered rule table. If both implementations agreed because they called the same
helper, a shared bug would be invisible; keep them apart.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime

from agentcheck.schemas.domain import Order, World

WINDOW_SECONDS = 30 * 24 * 60 * 60  # 2_592_000, inclusive
ACCEPTED_REASONS = ("damaged", "wrong_item", "changed_mind")


class FixtureError(ValueError):
    """Fixture data violates a policy precondition (e.g. delivery in the future)."""


@dataclass(frozen=True)
class OracleDecision:
    accessible: bool
    status: str | None
    eligible: bool | None
    reason_code: str
    refund_amount_cents: int | None


def epoch_seconds(timestamp: str) -> int:
    parsed = datetime.fromisoformat(timestamp)
    offset = parsed.utcoffset()
    if offset is None:
        raise FixtureError(f"timestamp without UTC offset: {timestamp}")
    if parsed.microsecond:
        raise FixtureError(f"sub-second timestamps are not used in fixtures: {timestamp}")
    return calendar.timegm(parsed.utctimetuple())


def _orders_for(world: World, customer_id: str) -> dict[str, Order]:
    return {o.order_id: o for o in world.orders if o.customer_id == customer_id}


def accessible_order(world: World, customer_id: str, order_id: str) -> Order | None:
    return _orders_for(world, customer_id).get(order_id.strip().upper())


def decide(world: World, customer_id: str, order_id: str, reason: str, clock_utc: str) -> OracleDecision:
    order = _orders_for(world, customer_id).get(order_id.strip().upper())
    if order is None:
        return OracleDecision(False, None, None, "not_found", None)

    normalized_reason = reason.strip().lower()
    age: int | None = None
    if order.delivered_at is not None:
        age = epoch_seconds(clock_utc) - epoch_seconds(order.delivered_at)
        if age < 0 and order.status == "delivered":
            raise FixtureError(f"{order.order_id} delivered after the case clock")

    rules: list[tuple[str, bool]] = [
        ("invalid_reason", normalized_reason not in ACCEPTED_REASONS),
        ("already_refunded", order.status == "refunded"),
        ("cancelled", order.status == "cancelled"),
        ("not_delivered", order.status != "delivered" or age is None),
        ("final_sale", order.final_sale),
        ("outside_window", age is not None and age > WINDOW_SECONDS),
    ]
    for code, triggered in rules:
        if triggered:
            if code == "invalid_reason":
                return OracleDecision(True, order.status, None, code, None)
            return OracleDecision(True, order.status, False, code, 0)
    return OracleDecision(True, order.status, True, "eligible", order.paid_cents)
