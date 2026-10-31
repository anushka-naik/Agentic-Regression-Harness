"""Opt-in live test against a local Ollama server. Never runs in default CI.

    uv run pytest -m live
"""

from __future__ import annotations

import os

import pytest

from agentcheck.model_clients.ollama import OllamaModelClient
from agentcheck.runner.execution import execute_trial
from tests.conftest import PROMPT, scripted_config

pytestmark = pytest.mark.live
MODEL = os.environ.get("AGENTCHECK_LIVE_MODEL", "qwen3:4b-q4_K_M")
URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


async def test_live_single_case_executes(case) -> None:  # type: ignore[no-untyped-def]
    client = OllamaModelClient(URL)
    fingerprint = await client.model_fingerprint(MODEL)
    assert fingerprint["digest"] and len(fingerprint["digest"]) == 64
    trial = await execute_trial(case("order_status_dev_001_a"),
                                scripted_config(provider="ollama", model=MODEL), client, PROMPT,
                                repeat_index=1, seed=42)
    # The outcome is whatever the model does; the harness must score it either way.
    assert trial.status in ("passed", "failed")
    assert trial.evaluation_mode == "live_model" and trial.checks
