from __future__ import annotations

import asyncio

import pytest

from agentcheck.errors import ModelUnavailableError
from agentcheck.model_clients.base import ModelConfig, ModelResponse
from agentcheck.model_clients.scripted import ScriptedModelClient
from agentcheck.runner.execution import execute_trial
from tests.conftest import PROMPT, action, scripted_config, tool


async def run(loaded, responses, **cfg):  # type: ignore[no-untyped-def]
    return await execute_trial(loaded, scripted_config(**cfg), ScriptedModelClient(responses), PROMPT,
                               repeat_index=1, seed=42)


def check(trial, code):  # type: ignore[no-untyped-def]
    return next(c for c in trial.checks if c.code == code)


ELIGIBLE_1001 = dict(outcome="eligible", order_id="O-1001", order_status="delivered", eligible=True,
                     refund_amount_cents=12900, currency="USD", reason_code="eligible",
                     evidence_event_ids=["tool-0001", "tool-0002"])


async def test_correct_trajectory_passes(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("eligible_refund_dev_001_a"), [
        tool("get_order", order_id="O-1001"),
        tool("check_refund_eligibility", order_id="O-1001", reason="damaged"),
        action(**ELIGIBLE_1001),
    ])
    assert trial.status == "passed", [c.message for c in trial.failed_checks]


async def test_malformed_json_gets_one_visible_repair(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("eligible_refund_dev_001_a"), [
        "{not json",
        tool("get_order", order_id="O-1001"),
        tool("check_refund_eligibility", order_id="O-1001", reason="damaged"),
        action(**ELIGIBLE_1001),
    ])
    types = [e.event_type for e in trial.events]
    assert types.count("protocol_error") == 1 and types.count("repair_requested") == 1
    assert trial.metrics.repairs == 1
    assert (trial.metrics.first_attempt_valid, trial.metrics.first_attempt_total) == (2, 3)
    # The repair does not erase the violation: protocol gate fails, other evidence still collected.
    assert check(trial, "protocol_valid").status == "fail"
    assert check(trial, "required_fields").status == "pass"
    repair_request = next(e for e in trial.events if e.event_type == "model_request" and e.payload["repair"])
    assert "format_error" in repair_request.payload["messages"][1]["content"]


async def test_second_malformed_action_is_agent_failure(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("eligible_refund_dev_001_a"), ["nope", "still nope"])
    assert trial.status == "failed" and trial.termination_reason == "protocol_error"


async def test_unregistered_tool_is_blocked_and_flagged(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("unsupported_action_dev_001_a"), [
        tool("cancel_order", order_id="O-1001"),
        action(outcome="unsupported", message="I can't cancel orders."),
    ])
    allow = check(trial, "tool_allowlist")
    assert allow.status == "fail" and "blocked, not executed" in allow.message
    assert trial.metrics.forbidden_tool_attempts == 1 and trial.metrics.forbidden_tool_executions == 0


async def test_model_call_limit_is_an_agent_failure(case) -> None:  # type: ignore[no-untyped-def]
    loop = [tool("get_order", order_id="O-1001")] * 10
    trial = await run(case("order_status_dev_001_a"), loop, max_model_calls_per_turn=3,
                      max_tool_attempts_per_turn=6)
    assert trial.status == "failed" and trial.termination_reason == "model_call_limit_turn"
    assert check(trial, "tool_budget").status == "fail"


async def test_context_budget_refuses_to_truncate(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("order_status_dev_001_a"), [action()], num_ctx=600, num_predict=500)
    assert trial.status == "failed" and trial.termination_reason == "context_budget_exceeded"


async def test_provider_outage_is_infrastructure_not_failure(case) -> None:  # type: ignore[no-untyped-def]
    class Down:
        provider = "ollama"

        async def next_action(self, messages, schema, config):  # type: ignore[no-untyped-def]
            raise ModelUnavailableError("connection refused")

    trial = await execute_trial(case("order_status_dev_001_a"), scripted_config(), Down(), PROMPT,
                                repeat_index=1, seed=42)
    assert trial.status == "infrastructure_error" and trial.checks == []


async def test_operator_cancellation_status(case) -> None:  # type: ignore[no-untyped-def]
    class Cancelled:
        provider = "scripted"

        async def next_action(self, messages, schema, config):  # type: ignore[no-untyped-def]
            raise asyncio.CancelledError

    trial = await execute_trial(case("order_status_dev_001_a"), scripted_config(), Cancelled(), PROMPT,
                                repeat_index=1, seed=42)
    assert trial.status == "cancelled"


async def test_planned_tool_timeout_is_scenario_not_infrastructure(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("persistent_tool_failure_dev_001_a"), [
        tool("get_order", order_id="O-1001"), tool("get_order", order_id="O-1001"),
        action(outcome="unavailable", order_id="O-1001", evidence_event_ids=["tool-0001", "tool-0002"]),
    ])
    assert trial.status == "passed"


async def test_missing_order_id_clarification_then_answer(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("missing_order_id_dev_001_a"), [
        action(outcome="needs_info", requested_field="order_id"),
        tool("get_order", order_id="O-1001"),
        tool("check_refund_eligibility", order_id="O-1001", reason="damaged"),
        action(**ELIGIBLE_1001),
    ])
    assert trial.status == "passed", [c.message for c in trial.failed_checks]


async def test_unexpected_intermediate_response_terminates_dialogue(case) -> None:  # type: ignore[no-untyped-def]
    trial = await run(case("missing_order_id_dev_001_a"), [action(outcome="unsupported")])
    assert trial.status == "failed"
    assert check(trial, "dialogue_complete").status == "fail"
    assert sum(e.event_type == "user_message" for e in trial.events) == 1


async def test_seed_and_think_are_sent() -> None:
    seen: list[ModelConfig] = []

    class Spy:
        provider = "scripted"

        async def next_action(self, messages, schema, config):  # type: ignore[no-untyped-def]
            seen.append(config)
            return ModelResponse(content=action(), latency_ms=0)

    from agentcheck.dataset import load_dataset
    from tests.conftest import DATASET

    loaded = load_dataset(DATASET).select(["unsupported_action_dev_001_a"])[0]
    await execute_trial(loaded, scripted_config(), Spy(), PROMPT, repeat_index=2, seed=43)
    assert seen[0].seed == 43 and seen[0].think is False and seen[0].temperature == 0


@pytest.mark.parametrize("case_id", ["order_status_dev_001_a", "multi_turn_correction_dev_001_b"])
async def test_model_inputs_never_contain_grading_metadata(case, case_id) -> None:  # type: ignore[no-untyped-def]
    from agentcheck.model_clients.scripted import ReferencePolicyClient

    loaded = case(case_id)
    trial = await execute_trial(loaded, scripted_config(), ReferencePolicyClient(), PROMPT,
                                repeat_index=1, seed=42)
    blob = "".join(str(e.payload["messages"]) for e in trial.events if e.event_type == "model_request")
    for token in (loaded.case.case_id, loaded.case.scenario_family_id, loaded.case.semantic_parent_id,
                  "expected_turns", "fault_plan", "after_response", "C-001"):
        assert token not in blob
    if loaded.case.category != "order_status":  # order_status is also a protocol outcome name
        assert loaded.case.category not in blob
    assert check(trial, "no_fixture_leakage").status == "pass"
