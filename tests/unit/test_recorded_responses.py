"""Replay recorded raw Qwen3-4B responses offline (fixture mode).

The responses were captured from a genuine live run; replaying them through the
scripted client must reproduce the live verdicts exactly. This exercises the
scorer on real model output in CI without downloading a model.
"""

from __future__ import annotations

import json

import pytest

from agentcheck.model_clients.scripted import ScriptedModelClient
from agentcheck.runner.execution import execute_trial
from tests.conftest import PROMPT, ROOT, scripted_config

FIXTURE = json.loads((ROOT / "tests/fixtures/model_responses/qwen3-4b-smoke-r1.json").read_text())


def test_fixture_provenance_is_recorded() -> None:
    p = FIXTURE["provenance"]
    assert len(p["digest"]) == 64 and p["model"] == "qwen3:4b-q4_K_M"


@pytest.mark.parametrize("case_id", sorted(FIXTURE["cases"]))
async def test_recorded_live_responses_reproduce_verdicts(case, case_id: str) -> None:  # type: ignore[no-untyped-def]
    recorded = FIXTURE["cases"][case_id]
    trial = await execute_trial(case(case_id), scripted_config(), ScriptedModelClient(recorded["responses"]),
                                PROMPT, repeat_index=1, seed=42)
    assert trial.status == recorded["live_status"]
    assert [c.code for c in trial.failed_checks] == recorded["failed_checks"]
