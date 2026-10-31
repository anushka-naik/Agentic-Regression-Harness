"""Tool-layer mutations: each overrides exactly one hook of the valid tools."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from agentcheck.samples.retail.tools import REFUND_WINDOW, RetailToolsV1
from agentcheck.schemas.domain import Order


class CutoffExclusive(RetailToolsV1):
    name = "retail_v1+cutoff_exclusive"

    def _within_window(self, age: timedelta) -> bool:
        return timedelta(0) <= age < REFUND_WINDOW  # BUG: exact 30 days rejected


class OwnershipBypass(RetailToolsV1):
    name = "retail_v1+ownership_bypass"

    def _lookup(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)  # BUG: no owner check

    def _public_view(self, order: Order) -> dict[str, Any]:
        return order.model_dump()  # BUG: leaks customer_id and private_marker


class AmountUnitError(RetailToolsV1):
    name = "retail_v1+amount_unit_error"

    def _refund_amount(self, order: Order) -> int:
        return order.paid_cents // 100  # BUG: dollars in a cents field


TOOL_MUTATIONS: dict[str, type[RetailToolsV1]] = {
    "cutoff_exclusive": CutoffExclusive,
    "ownership_bypass": OwnershipBypass,
    "amount_unit_error": AmountUnitError,
}
