from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from agentcheck.canonical import canonical_json, sha256_json
from agentcheck.runner.faults import FaultSchedule
from agentcheck.runner.trace import TraceSink
from agentcheck.samples.retail.tools import RetailToolsV1
from agentcheck.schemas.cases import EvaluationCase, FaultPlanEntry
from agentcheck.schemas.protocol import AgentAction, action_json_schema
from agentcheck.tools.dispatcher import ToolDispatcher

NOW = datetime(2026, 1, 31, 12, tzinfo=UTC)


def dispatcher(dataset, plan=None):  # type: ignore[no-untyped-def]
    world = dataset.worlds["worlds/seed_store.json"]
    trace = TraceSink()
    faults = FaultSchedule([FaultPlanEntry.model_validate(p) for p in plan or []])
    return ToolDispatcher(RetailToolsV1(world, "C-001", NOW), faults, trace), trace


def test_unknown_tool_logged_before_rejection(dataset) -> None:  # type: ignore[no-untyped-def]
    d, trace = dispatcher(dataset)
    obs = d.dispatch("issue_refund", {"order_id": "O-1001"}, turn=1)
    assert [e.event_type for e in trace.events] == ["tool_attempt", "tool_rejected"]
    assert obs["result"]["error"]["code"] == "unknown_tool"
    assert trace.events[1].payload["executed"] is False


def test_model_cannot_supply_identity(dataset) -> None:  # type: ignore[no-untyped-def]
    d, _ = dispatcher(dataset)
    obs = d.dispatch("get_order", {"order_id": "O-1002", "customer_id": "C-002"}, turn=1)
    assert obs["result"]["error"]["code"] == "invalid_arguments"
    assert "order" not in obs["result"]


def test_arguments_are_normalised(dataset) -> None:  # type: ignore[no-untyped-def]
    d, _ = dispatcher(dataset)
    obs = d.dispatch("check_refund_eligibility", {"order_id": " o-1001 ", "reason": "Damaged"}, turn=1)
    assert obs["arguments"] == {"order_id": "O-1001", "reason": "damaged"}
    assert obs["result"]["eligible"] is True


def test_event_ids_are_host_generated(dataset) -> None:  # type: ignore[no-untyped-def]
    d, _ = dispatcher(dataset)
    ids = [d.dispatch("get_order", {"order_id": "O-1001"}, turn=1)["event_id"] for _ in range(3)]
    assert ids == ["tool-0001", "tool-0002", "tool-0003"]


def test_fault_schedule_is_deterministic_per_attempt(dataset) -> None:  # type: ignore[no-untyped-def]
    plan = [{"tool_name": "get_order", "arguments": {"order_id": "o-1001"},
             "attempts": [{"index": 1, "error": {"code": "timeout"}}]}]
    for _ in range(2):  # a fresh schedule behaves identically each time
        d, trace = dispatcher(dataset, plan)
        first = d.dispatch("get_order", {"order_id": "O-1001"}, turn=1)
        second = d.dispatch("get_order", {"order_id": "O-1001"}, turn=1)
        other = d.dispatch("get_order", {"order_id": "O-1003"}, turn=1)
        assert first["result"] == {"ok": False, "error": {"code": "timeout", "retryable": True}}
        assert second["result"]["ok"] and other["result"]["ok"]
        assert [e.event_type for e in trace.events if e.event_type.startswith("tool_")
                and e.event_type != "tool_attempt"] == ["tool_error", "tool_result", "tool_result"]


def test_action_envelope_shape_rules() -> None:
    with pytest.raises(ValidationError):
        AgentAction.model_validate({"action": "tool_call", "tool_name": None, "arguments": {}, "response": None})
    with pytest.raises(ValidationError):
        AgentAction.model_validate({"action": "final", "tool_name": "get_order", "arguments": None,
                                    "response": None})
    ok = AgentAction.model_validate({"action": "tool_call", "tool_name": "anything_goes",
                                     "arguments": {}, "response": None})
    assert ok.tool_name == "anything_goes"  # not an enum: unknown tools stay observable


def test_schema_is_inlined_and_does_not_force_answers() -> None:
    schema = action_json_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    tool_branch, final_branch = schema["anyOf"]
    assert tool_branch["properties"]["tool_name"] == {"type": "string"}
    assert final_branch["properties"]["tool_name"] == {"type": "null"}
    response = final_branch["properties"]["response"]
    assert response["properties"]["refund_issued"] == {"type": "boolean"}


@pytest.mark.parametrize("bad", ["/etc/passwd", "../outside.json", "worlds/../../x.json", "worlds\\x.json"])
def test_case_paths_must_stay_in_dataset(dataset, bad: str) -> None:  # type: ignore[no-untyped-def]
    raw = dataset.cases[0].model_dump(mode="json")
    raw["world_file"] = bad
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(raw)


@given(st.dictionaries(st.text(min_size=1, max_size=8), st.integers() | st.text(max_size=8), max_size=8))
def test_canonical_hash_ignores_key_order(d: dict) -> None:  # type: ignore[type-arg]
    reordered = dict(reversed(list(d.items())))
    assert canonical_json(d) == canonical_json(reordered)
    assert sha256_json(d) == sha256_json(reordered)


@given(st.integers(0, 7))
def test_world_clone_cannot_mutate_fixture(dataset, index: int) -> None:  # type: ignore[no-untyped-def]
    original = dataset.worlds["worlds/seed_store.json"]
    before = sha256_json(original)
    clone = original.model_copy(deep=True)
    clone.orders.pop(index)
    assert sha256_json(original) == before and len(original.orders) == 8
