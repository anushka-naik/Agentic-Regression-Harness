"""Database-backed behaviour: import, recovery, replay and the dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentcheck.comparisons.metrics import RunRecord
from agentcheck.comparisons.reports import read_run, write_run
from agentcheck.errors import IncompatibleRunsError
from agentcheck.runner.execution import latest_trials
from agentcheck.settings import Settings
from agentcheck.web.app import create_app
from agentcheck.workflows import (
    Workspace,
    configure,
    experiment,
    import_run,
    prepare_run,
    replay,
    resume,
    runner,
)
from tests.conftest import ROOT

pytestmark = pytest.mark.db
GATE = Path("configs/gates/default.yaml")


def ws(repo: Any) -> Workspace:
    settings = Settings(AGENTCHECK_PROJECT_ROOT=ROOT, DATABASE_URL="unused")  # type: ignore[call-arg]
    return Workspace(settings, repo, lambda _: None)


async def test_experiment_persists_and_dashboard_escapes(repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    w = ws(repo)
    result = await experiment(w, Path("configs/agents/baseline.yaml"), Path("configs/agents/candidate.yaml"),
                              "heldout", 1, tmp_path, GATE, mode="fixture",
                              case_ids=["untrusted_tool_note_heldout_005_a"], progress=False)
    assert result.gate["status"] == "PASS"
    client = TestClient(create_app(repo))
    assert client.get("/healthz").json()["database"] is True
    runs = client.get("/api/runs", params={"size": 500})
    assert runs.status_code == 422  # page size is bounded
    runs = client.get("/api/runs").json()
    assert runs["total"] == 2
    attempt = client.get(f"/api/runs/{result.candidate.run_id}/cases").json()["items"][0]["attempt_id"]
    page = client.get(f"/attempts/{attempt}")
    assert page.status_code == 200
    # The fixture note contains <script>alert('northstar')</script>; it must render escaped.
    assert "<script>alert" not in page.text
    assert "&lt;script&gt;alert" in page.text
    assert client.get("/attempts/not-a-uuid").status_code == 422
    assert client.get(f"/comparisons/{result.comparison_id}").status_code == 200
    report = client.get(f"/api/comparisons/{result.comparison_id}/report")
    assert report.status_code == 200 and "Gate: PASS" in report.text


async def test_import_is_idempotent_and_rejects_conflicts(repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    w = ws(None)  # run without persistence, then import
    cfg = configure(Path("configs/agents/baseline.yaml"), "fixture", None, ROOT)
    state = await prepare_run(w, cfg, "baseline.yaml", "baseline", w.dataset(), "smoke", 1,
                              ["order_status_dev_001_a", "eligible_refund_dev_001_a"])
    await runner(w, progress=False).run(state)
    path = write_run(RunRecord.from_state(state), tmp_path / "run")

    w = ws(repo)
    assert import_run(w, path) == "imported"
    assert import_run(w, path) == "unchanged"
    loaded = repo.load_record(state.run_id)
    assert len(loaded.trials) == 2 and all(t.events for t in loaded.trials)

    record, _ = read_run(path)
    record.trials = record.trials[:1]
    conflict = write_run(record, tmp_path / "conflict")
    with pytest.raises(IncompatibleRunsError):
        import_run(w, conflict)


async def test_interrupted_run_resumes_without_overwriting(repo) -> None:  # type: ignore[no-untyped-def]
    w = ws(repo)
    cfg = configure(Path("configs/agents/baseline.yaml"), "fixture", None, ROOT)
    ids = ["order_status_dev_001_a", "eligible_refund_dev_001_a", "time_boundary_dev_001_a"]
    state = await prepare_run(w, cfg, "configs/agents/baseline.yaml", "single", w.dataset(), "smoke", 1, ids)
    r = runner(w, progress=False)
    client_cases = state.cases
    # Complete only the first trial, then simulate a crash mid-way through the second.
    state.cases = client_cases[:1]
    await r.run(state)
    state.cases = client_cases
    repo.begin_attempt(state.run_id, client_cases[1], 1, 42, 1)  # left "running"
    repo.set_run_status(state.run_id, "running")
    before = repo.load_record(state.run_id)
    first_done = next(t for t in before.trials if t.case_id == ids[0])

    record = await resume(w, state.run_id)
    assert record.status == "completed"
    latest = latest_trials(record.trials)
    assert len(latest) == 3 and all(t.status == "passed" for t in latest.values())
    kept = next(t for t in record.trials if t.case_id == ids[0])
    assert kept.events[0].wall_time == first_done.events[0].wall_time  # not re-run
    cases, _ = repo.run_cases(state.run_id)
    second = [c for c in cases if c["case_id"] == ids[1]]
    assert sorted((c["attempt_index"], c["status"]) for c in second) == [(1, "abandoned"), (2, "passed")]
    assert second[-1]["rerun_reason"] == "resume after abandoned"


async def test_replay_reproduces_deterministic_scores(repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    w = ws(None)
    result = await experiment(w, Path("configs/agents/baseline.yaml"), Path("configs/agents/baseline.yaml"),
                              "smoke", 1, tmp_path, GATE, mode="fixture",
                              candidate_mutations=["stale_order_evidence"], progress=False)
    for side in ("baseline", "candidate"):
        summary = replay(w, str(tmp_path / "runs" / side / "run.json"))
        assert summary["mismatches"] == 0 and summary["trials"] == 12
    assert result.gate["status"] == "FAIL"
