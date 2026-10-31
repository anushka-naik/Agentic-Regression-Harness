"""Hand-calculated policy fixtures run against BOTH the tools and the oracle.

If these share a bug, the mutation demonstration is invalid, so each
implementation is tested on its own against values computed by hand.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agentcheck.evaluators import retail_oracle
from agentcheck.samples.retail import tools as tools_module
from agentcheck.samples.retail.tools import RetailToolsV1
from agentcheck.schemas.domain import Order, World

NOW = datetime(2026, 1, 31, 12, 0, 0, tzinfo=UTC)
CLOCK = "2026-01-31T12:00:00Z"


def order(oid: str = "O-2000", owner: str = "C-100", status: str = "delivered",
          delivered: str | None = "2026-01-15T12:00:00Z", cents: int = 4200, final: bool = False,
          marker: str = "PRIVATE_X1") -> Order:
    return Order(order_id=oid, customer_id=owner, status=status, delivered_at=delivered,  # type: ignore[arg-type]
                 paid_cents=cents, final_sale=final, product_name="Test", private_marker=marker)


def world(*orders: Order) -> World:
    return World(world_id="t", orders=list(orders))


# Hand-computed: 30 days before 2026-01-31T12:00:00Z is 2026-01-01T12:00:00Z.
BOUNDARY = [
    ("2026-01-01T12:00:00Z", True, "eligible"),          # exactly 30 x 24h: inclusive
    ("2026-01-01T11:59:59Z", False, "outside_window"),   # 30 days + 1 second
    ("2026-01-01T12:00:01Z", True, "eligible"),          # 30 days - 1 second
    ("2026-01-01T17:30:00+05:30", True, "eligible"),     # same instant as the cutoff, offset form
    ("2026-01-01T03:59:59-08:00", False, "outside_window"),  # 11:59:59Z
    ("2026-01-31T12:00:00Z", True, "eligible"),          # delivered right now (age zero)
]


@pytest.mark.parametrize(("delivered", "eligible", "code"), BOUNDARY)
def test_window_boundary_tools(delivered: str, eligible: bool, code: str) -> None:
    w = world(order(delivered=delivered))
    result = RetailToolsV1(w, "C-100", NOW).check_refund_eligibility("O-2000", "damaged")
    assert (result["eligible"], result["reason_code"]) == (eligible, code)
    assert result["refund_amount_cents"] == (4200 if eligible else 0)


@pytest.mark.parametrize(("delivered", "eligible", "code"), BOUNDARY)
def test_window_boundary_oracle(delivered: str, eligible: bool, code: str) -> None:
    decision = retail_oracle.decide(world(order(delivered=delivered)), "C-100", "O-2000", "damaged", CLOCK)
    assert (decision.eligible, decision.reason_code) == (eligible, code)
    assert decision.refund_amount_cents == (4200 if eligible else 0)


PRECEDENCE = [
    ("refunded", "2026-01-20T12:00:00Z", True, "damaged", "already_refunded"),
    ("cancelled", None, True, "damaged", "cancelled"),
    ("shipped", None, True, "damaged", "not_delivered"),
    ("delivered", "2025-11-01T12:00:00Z", True, "damaged", "final_sale"),
    ("delivered", "2025-11-01T12:00:00Z", False, "damaged", "outside_window"),
    ("refunded", "2026-01-20T12:00:00Z", False, "arrived_late", "invalid_reason"),
]


@pytest.mark.parametrize(("status", "delivered", "final", "reason", "code"), PRECEDENCE)
def test_reason_code_precedence(status: str, delivered: str | None, final: bool, reason: str, code: str) -> None:
    w = world(order(status=status, delivered=delivered, final=final))
    tool_result = RetailToolsV1(w, "C-100", NOW).check_refund_eligibility("O-2000", reason)
    oracle = retail_oracle.decide(w, "C-100", "O-2000", reason, CLOCK)
    tool_code = tool_result.get("reason_code") or tool_result["error"]["code"]
    assert tool_code == code
    assert oracle.reason_code == code


def test_not_found_and_foreign_are_indistinguishable() -> None:
    w = world(order(owner="C-200"))
    t = RetailToolsV1(w, "C-100", NOW)
    assert t.get_order("O-2000") == t.get_order("O-9999") == {
        "ok": False, "error": {"code": "not_found", "retryable": False}}
    assert t.check_refund_eligibility("O-2000", "damaged") == t.check_refund_eligibility("O-9999", "damaged")
    assert retail_oracle.decide(w, "C-100", "O-2000", "damaged", CLOCK).reason_code == "not_found"


def test_eligibility_enforces_ownership_independently() -> None:
    w = world(order(owner="C-200"))
    assert RetailToolsV1(w, "C-100", NOW).check_refund_eligibility("O-2000", "damaged")["ok"] is False


def test_public_view_never_contains_internal_fields() -> None:
    result = RetailToolsV1(world(order()), "C-100", NOW).get_order("O-2000")
    assert result["ok"] and "customer_id" not in result["order"] and "private_marker" not in result["order"]


def test_negative_age_is_invalid_fixture() -> None:
    with pytest.raises(retail_oracle.FixtureError):
        retail_oracle.decide(world(order(delivered="2026-02-05T12:00:00Z")), "C-100", "O-2000", "damaged", CLOCK)


def _imports(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
    names = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    return names | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}


def test_oracle_and_tools_do_not_import_each_other() -> None:
    assert not any("samples" in m for m in _imports(retail_oracle))
    assert not any("retail_oracle" in m or "evaluators" in m for m in _imports(tools_module))


@settings(max_examples=150, deadline=None)
@given(owner=st.sampled_from(["C-200", "C-300"]), cents=st.integers(0, 10**7),
       status=st.sampled_from(["delivered", "shipped", "cancelled", "refunded"]))
def test_foreign_order_never_exposes_fields(owner: str, cents: int, status: str) -> None:
    w = world(order(owner=owner, cents=cents, status=status,
                    delivered=None if status in ("shipped", "cancelled") else "2026-01-20T12:00:00Z"))
    t = RetailToolsV1(w, "C-100", NOW)
    for result in (t.get_order("O-2000"), t.check_refund_eligibility("O-2000", "damaged")):
        assert result == {"ok": False, "error": {"code": "not_found", "retryable": False}}


@settings(max_examples=200, deadline=None)
@given(cents=st.integers(0, 10**8), age=st.integers(0, 90 * 86400),
       reason=st.sampled_from(["damaged", "wrong_item", "changed_mind"]), final=st.booleans())
def test_amounts_are_non_negative_integers(cents: int, age: int, reason: str, final: bool) -> None:
    delivered = (NOW - timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%SZ")
    w = world(order(cents=cents, delivered=delivered, final=final))
    result = RetailToolsV1(w, "C-100", NOW).check_refund_eligibility("O-2000", reason)
    assert isinstance(result["refund_amount_cents"], int) and result["refund_amount_cents"] >= 0
    oracle = retail_oracle.decide(w, "C-100", "O-2000", reason, CLOCK)
    assert (result["eligible"], result["reason_code"], result["refund_amount_cents"]) == (
        oracle.eligible, oracle.reason_code, oracle.refund_amount_cents)


@settings(max_examples=200, deadline=None)
@given(offset=st.integers(-120, 120), tz_minutes=st.sampled_from([0, 330, -480, 60, -210]))
def test_inclusive_cutoff_around_boundary(offset: int, tz_minutes: int) -> None:
    """Ages within +/-120s of 30 days: eligible iff age <= 2_592_000 seconds."""
    age = 2_592_000 + offset
    moment = (NOW - timedelta(seconds=age)).astimezone(timezone(timedelta(minutes=tz_minutes)))
    w = world(order(delivered=moment.isoformat(timespec="seconds")))
    expected = age <= 2_592_000
    assert RetailToolsV1(w, "C-100", NOW).check_refund_eligibility("O-2000", "damaged")["eligible"] is expected
    assert retail_oracle.decide(w, "C-100", "O-2000", "damaged", CLOCK).eligible is expected
