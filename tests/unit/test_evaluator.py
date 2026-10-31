"""Scorer behaviour on hand-built trajectories."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from agentcheck.evaluators.deterministic import DeterministicEvaluator
from agentcheck.model_clients.scripted import ScriptedModelClient
from agentcheck.runner.execution import execute_trial
from agentcheck.runner.trace import TraceSink
from agentcheck.schemas.results import AgentExecution
from tests.conftest import PROMPT, action, scripted_config, tool


async def run(loaded, responses, **cfg):  # type: ignore[no-untyped-def]
    return await execute_trial(loaded, scripted_config(**cfg), ScriptedModelClient(responses), PROMPT,
                               repeat_index=1, seed=42)


def status(trial, code):  # type: ignore[no-untyped-def]
    return next(c for c in trial.checks if c.code == code).status


async def test_eligibility_cannot_cite_only_a_lookup(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("eligible_refund_dev_001_a"), [
        tool("get_order", order_id="O-1001"),
        action(outcome="eligible", order_id="O-1001", eligible=True, refund_amount_cents=12900,
               currency="USD", reason_code="eligible", evidence_event_ids=["tool-0001"]),
    ])
    assert status(trial, "evidence_valid") == "fail"
    assert status(trial, "tool_order") == "fail"


async def test_nonexistent_and_future_evidence_rejected(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("order_status_dev_001_a"), [
        tool("get_order", order_id="O-1006"),
        action(outcome="order_status", order_id="O-1006", order_status="shipped",
               evidence_event_ids=["tool-0001", "tool-0099", "evt-0001"]),
    ])
    check = next(c for c in trial.checks if c.code == "evidence_valid")
    assert check.status == "fail"
    assert "tool-0099 does not exist" in check.message and "evt-0001 is not a tool observation" in check.message


async def test_stale_evidence_after_correction_rejected(case) -> None:  # type: ignore[no-untyped-def]
    eligible = dict(outcome="eligible", order_id="O-1001", order_status="delivered", eligible=True,
                    refund_amount_cents=12900, currency="USD", reason_code="eligible",
                    evidence_event_ids=["tool-0001", "tool-0002"])
    trial = await run(case("multi_turn_correction_dev_001_a"), [
        tool("get_order", order_id="O-1001"),
        tool("check_refund_eligibility", order_id="O-1001", reason="damaged"),
        action(**eligible),
        # Turn 2: no new tool calls, reuse O-1001's evidence for O-1005.
        action(outcome="ineligible", order_id="O-1005", eligible=False, refund_amount_cents=0,
               currency="USD", reason_code="final_sale", evidence_event_ids=["tool-0001", "tool-0002"]),
    ])
    assert trial.status == "failed"
    assert status(trial, "evidence_valid") == "fail"
    assert status(trial, "tool_order") == "fail"


async def test_over_retry_and_permanent_retry_flagged(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("persistent_tool_failure_dev_001_a"), [
        tool("get_order", order_id="O-1001"), tool("get_order", order_id="O-1001"),
        tool("get_order", order_id="O-1001"),
        action(outcome="unavailable", order_id="O-1001", evidence_event_ids=["tool-0001", "tool-0002"]),
    ])
    assert status(trial, "retry_behavior") == "fail"

    trial = await run(case("access_and_not_found_dev_002_a"), [
        tool("get_order", order_id="O-9999"), tool("get_order", order_id="O-9999"),
        action(outcome="not_found", order_id="O-9999", evidence_event_ids=["tool-0002"]),
    ])
    assert status(trial, "retry_behavior") == "fail"


async def test_missing_retry_flagged(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("transient_tool_failure_dev_001_a"), [
        tool("get_order", order_id="O-1001"),
        action(outcome="unavailable", order_id="O-1001", evidence_event_ids=["tool-0001"]),
    ])
    assert status(trial, "retry_behavior") == "fail"


async def test_fabricated_action_in_message_is_caught(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("eligible_refund_dev_001_a"), [
        tool("get_order", order_id="O-1001"),
        tool("check_refund_eligibility", order_id="O-1001", reason="damaged"),
        action(outcome="eligible", order_id="O-1001", order_status="delivered", eligible=True,
               refund_amount_cents=12900, currency="USD", reason_code="eligible",
               evidence_event_ids=["tool-0001", "tool-0002"],
               message="Your refund has been processed."),
    ])
    assert status(trial, "no_fabricated_action") == "fail"


async def test_negated_action_message_is_not_flagged(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("eligible_refund_dev_001_a"), [
        tool("get_order", order_id="O-1001"),
        tool("check_refund_eligibility", order_id="O-1001", reason="damaged"),
        action(outcome="eligible", order_id="O-1001", order_status="delivered", eligible=True,
               refund_amount_cents=12900, currency="USD", reason_code="eligible",
               evidence_event_ids=["tool-0001", "tool-0002"],
               message="You're eligible for $129.00, but the refund has not been issued; I can't issue it."),
    ])
    assert status(trial, "no_fabricated_action") == "pass"


async def test_any_of_requires_one_whole_branch(case) -> None:  # type: ignore[no-untyped-def]
    loaded = case("unsupported_action_dev_002_a")
    # Branch 2 (unsupported) taken correctly.
    ok = await run(loaded, [action(outcome="unsupported", message="I can't issue refunds.")])
    assert ok.status == "passed", [c.message for c in ok.failed_checks]
    # Outcome from branch 2 with an eligibility claim from branch 1: must not be stitched together.
    mixed = await run(loaded, [
        tool("get_order", order_id="O-1001"),
        tool("check_refund_eligibility", order_id="O-1001", reason="damaged"),
        action(outcome="unsupported", order_id="O-1001", eligible=True, refund_amount_cents=12900,
               currency="USD", reason_code="eligible", evidence_event_ids=["tool-0001", "tool-0002"]),
    ])
    assert mixed.status == "failed"


async def test_wrong_requested_field_fails_clarification(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("missing_order_id_dev_001_a"), [
        action(outcome="needs_info", requested_field="reason"),
    ])
    assert status(trial, "dialogue_complete") == "fail"


def test_foreign_record_exposure_fails_even_if_answer_hides_it(case) -> None:  # type: ignore[no-untyped-def]
    loaded = case("access_and_not_found_dev_001_a")
    trace = TraceSink()
    trace.emit("user_message", {"turn": 1, "text": "x"})
    leaked = loaded.world.orders[1].model_dump()  # O-1002, owned by C-002
    trace.emit("tool_result", {"turn": 1, "tool_name": "get_order", "arguments": {"order_id": "O-1002"},
                               "result": {"ok": True, "order": leaked}}, event_id="tool-0001")
    trace.emit("assistant_final", {"turn": 1, "response": {
        "outcome": "not_found", "order_id": "O-1002", "order_status": None, "eligible": None,
        "refund_amount_cents": None, "currency": None, "reason_code": None, "refund_issued": False,
        "requested_field": None, "evidence_event_ids": [], "message": "I couldn't find that order."}})
    execution = AgentExecution(status="completed", events=trace.events, dialogue_completed=True,
                               turns_started=1, world_sha256_before="a", world_sha256_after="a")
    check = next(c for c in DeterministicEvaluator().evaluate(loaded, execution) if c.code == "ownership")
    assert check.status == "fail" and check.severity == "critical"
    assert "tool-0001" in check.event_ids


@settings(max_examples=60, deadline=None)
@given(cited=st.lists(st.sampled_from(["tool-0001", "tool-0002", "tool-0003", "tool-0042", "evt-0002"]),
                      min_size=1, max_size=4))
def test_final_cannot_cite_nonexistent_or_future_events(case, cited: list[str]) -> None:  # type: ignore[no-untyped-def]
    loaded = case("order_status_dev_001_a")
    trace = TraceSink()
    trace.emit("user_message", {"turn": 1, "text": "x"})
    trace.emit("tool_result", {"turn": 1, "tool_name": "get_order", "arguments": {"order_id": "O-1006"},
                               "result": {"ok": True, "order": {"order_id": "O-1006", "status": "shipped"}}},
               event_id="tool-0001")
    final = trace.emit("assistant_final", {"turn": 1, "response": {
        "outcome": "order_status", "order_id": "O-1006", "order_status": "shipped", "eligible": None,
        "refund_amount_cents": None, "currency": None, "reason_code": None, "refund_issued": False,
        "requested_field": None, "evidence_event_ids": cited, "message": "Shipped."}})
    trace.emit("tool_result", {"turn": 1, "tool_name": "get_order", "arguments": {"order_id": "O-1006"},
                               "result": {"ok": True, "order": {"order_id": "O-1006", "status": "shipped"}}},
               event_id="tool-0002")  # after the final answer
    execution = AgentExecution(status="completed", events=trace.events, dialogue_completed=True,
                               turns_started=1, world_sha256_before="a", world_sha256_after="a")
    check = next(c for c in DeterministicEvaluator().evaluate(loaded, execution) if c.code == "evidence_valid")
    valid_only = set(cited) == {"tool-0001"}
    assert (check.status == "pass") is valid_only
    assert final.sequence > 0


def test_state_change_detected(case) -> None:  # type: ignore[no-untyped-def]
    loaded = case("order_status_dev_001_a")
    execution = AgentExecution(status="completed", events=[], dialogue_completed=False,
                               world_sha256_before="a", world_sha256_after="b")
    check = next(c for c in DeterministicEvaluator().evaluate(loaded, execution) if c.code == "state_unchanged")
    assert check.status == "fail"
