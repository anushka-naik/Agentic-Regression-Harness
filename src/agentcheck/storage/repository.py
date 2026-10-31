"""Database access. All queries go through SQLAlchemy (parameterised)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, create_engine, func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from agentcheck.canonical import sha256_json
from agentcheck.comparisons.metrics import RunRecord
from agentcheck.errors import InfrastructureError
from agentcheck.runner.execution import RunState
from agentcheck.schemas.cases import LoadedCase
from agentcheck.schemas.results import TraceEvent, TrialResult
from agentcheck.storage.models import (
    AgentConfigRow,
    CaseAttempt,
    CheckResultRow,
    Comparison,
    DatasetVersion,
    JudgeReview,
    Run,
    TraceEventRow,
)

MAX_PAGE_SIZE = 100


def make_engine(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        raise InfrastructureError(
            f"cannot connect to the database ({exc.orig.__class__.__name__}). "
            "Start it with `docker compose up -d db` and run `uv run alembic upgrade head`."
        ) from exc
    return engine


class Repository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as session, session.begin():
            yield session

    def _dataset_id(self, s: Session, manifest: dict[str, Any]) -> int:
        digest = manifest["test"]["dataset_sha256"]
        row = s.scalar(select(DatasetVersion).where(DatasetVersion.content_sha256 == digest))
        if row is None:
            row = DatasetVersion(name=manifest["test"].get("dataset_name") or "unknown", schema_version=1,
                                 content_sha256=digest, manifest={"test": manifest["test"]})
            s.add(row)
            s.flush()
        return row.id

    def _config_id(self, s: Session, manifest: dict[str, Any]) -> int:
        agent = manifest["agent"]
        row = s.scalar(select(AgentConfigRow).where(AgentConfigRow.config_sha256 == agent["config_sha256"]))
        if row is None:
            row = AgentConfigRow(config_sha256=agent["config_sha256"], config=agent["config"],
                                 prompt_sha256=agent["prompt_sha256"],
                                 tool_revision=f"{agent['tool_implementation']}@{agent['tool_sha256'][:12]}")
            s.add(row)
            s.flush()
        return row.id

    def create_run(self, state: RunState) -> None:
        with self.session() as s:
            s.add(Run(id=uuid.UUID(state.run_id), dataset_version_id=self._dataset_id(s, state.manifest),
                      agent_config_id=self._config_id(s, state.manifest), side=state.side,
                      mode=state.manifest["test"]["evaluation_mode"], status="running",
                      manifest=state.manifest))

    def set_run_status(self, run_id: str, status: str) -> None:
        with self.session() as s:
            s.execute(update(Run).where(Run.id == uuid.UUID(run_id)).values(
                status=status, finished_at=datetime.now(UTC) if status != "running" else None))

    def get_run(self, run_id: str) -> Run | None:
        with self.session() as s:
            return s.get(Run, uuid.UUID(run_id))

    def list_runs(self, page: int = 1, size: int = 25) -> tuple[list[dict[str, Any]], int]:
        size = max(1, min(size, MAX_PAGE_SIZE))
        page = max(page, 1)
        with self.session() as s:
            total = s.scalar(select(func.count()).select_from(Run)) or 0
            runs = s.scalars(select(Run).order_by(Run.started_at.desc()).offset((page - 1) * size).limit(size)).all()
            out = []
            for run in runs:
                counts = dict(s.execute(
                    select(CaseAttempt.status, func.count()).where(CaseAttempt.run_id == run.id)
                    .group_by(CaseAttempt.status)).all())
                out.append(_run_dict(run, counts))
            return out, total

    def run_cases(self, run_id: str, page: int = 1, size: int = 50) -> tuple[list[dict[str, Any]], int]:
        size = max(1, min(size, MAX_PAGE_SIZE))
        rid = uuid.UUID(run_id)
        with self.session() as s:
            total = s.scalar(select(func.count()).select_from(CaseAttempt).where(CaseAttempt.run_id == rid)) or 0
            rows = s.scalars(select(CaseAttempt).where(CaseAttempt.run_id == rid)
                             .order_by(CaseAttempt.case_id, CaseAttempt.repeat_index, CaseAttempt.attempt_index)
                             .offset((max(page, 1) - 1) * size).limit(size)).all()
            return [_attempt_dict(a) for a in rows], total

    def attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with self.session() as s:
            a = s.get(CaseAttempt, uuid.UUID(attempt_id))
            if a is None:
                return None
            events = s.scalars(select(TraceEventRow).where(TraceEventRow.case_attempt_id == a.id)
                               .order_by(TraceEventRow.sequence)).all()
            checks = s.scalars(select(CheckResultRow).where(CheckResultRow.case_attempt_id == a.id)
                               .order_by(CheckResultRow.id)).all()
            reviews = s.scalars(select(JudgeReview).where(JudgeReview.case_attempt_id == a.id)).all()
            return {
                **_attempt_dict(a), "run_id": str(a.run_id),
                "events": [_event_dict(e) for e in events],
                "checks": [{"code": c.code, "status": c.status, "severity": c.severity,
                            "expected": c.expected, "actual": c.actual, "evidence": c.evidence} for c in checks],
                "judge": [{"verdict": r.verdict, "payload": r.payload,
                           "human_review_status": r.human_review_status} for r in reviews],
            }

    def begin_attempt(self, run_id: str, loaded: LoadedCase, repeat_index: int, seed: int,
                      attempt_index: int, rerun_reason: str | None = None) -> uuid.UUID:
        attempt_id = uuid.uuid4()
        with self.session() as s:
            s.add(CaseAttempt(id=attempt_id, run_id=uuid.UUID(run_id), case_id=loaded.case.case_id,
                              category=loaded.case.category, repeat_index=repeat_index,
                              attempt_index=attempt_index, seed=seed, status="running",
                              rerun_reason=rerun_reason))
        return attempt_id

    def add_event(self, attempt_id: uuid.UUID, event: TraceEvent) -> None:
        with self.session() as s:
            s.add(TraceEventRow(case_attempt_id=attempt_id, sequence=event.sequence, event_id=event.event_id,
                                event_type=event.event_type, payload=event.payload,
                                wall_time=event.wall_time, elapsed_ms=event.elapsed_ms))

    def finish_attempt(self, attempt_id: uuid.UUID, trial: TrialResult, persist_events: bool = False) -> None:
        """Finalise status and checks in one transaction."""
        with self.session() as s:
            a = s.get(CaseAttempt, attempt_id)
            assert a is not None
            a.status = trial.status
            a.latency_ms = trial.metrics.latency_ms
            a.finished_at = datetime.now(UTC)
            a.result = trial.model_dump(mode="json", exclude={"events"})
            if persist_events:
                for e in trial.events:
                    s.add(TraceEventRow(case_attempt_id=attempt_id, sequence=e.sequence, event_id=e.event_id,
                                        event_type=e.event_type, payload=e.payload, wall_time=e.wall_time,
                                        elapsed_ms=e.elapsed_ms))
            else:
                known = set(s.scalars(select(TraceEventRow.sequence).where(
                    TraceEventRow.case_attempt_id == attempt_id)).all())
                for e in trial.events:
                    if e.sequence not in known:
                        s.add(TraceEventRow(case_attempt_id=attempt_id, sequence=e.sequence, event_id=e.event_id,
                                            event_type=e.event_type, payload=e.payload, wall_time=e.wall_time,
                                            elapsed_ms=e.elapsed_ms))
            for c in trial.checks:
                s.add(CheckResultRow(case_attempt_id=attempt_id, code=c.code, status=c.status,
                                     severity=c.severity, expected=c.expected, actual=c.actual,
                                     evidence={"event_ids": c.event_ids, "message": c.message}))

    def abandon_interrupted(self, run_id: str) -> int:
        with self.session() as s:
            result = s.execute(update(CaseAttempt).where(
                CaseAttempt.run_id == uuid.UUID(run_id), CaseAttempt.status == "running"
            ).values(status="abandoned", finished_at=datetime.now(UTC)))
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def attempt_index_map(self, run_id: str) -> dict[tuple[str, int], tuple[int, str]]:
        """(case, repeat) -> (highest attempt index, status of best attempt)."""
        with self.session() as s:
            rows = s.execute(select(CaseAttempt.case_id, CaseAttempt.repeat_index, CaseAttempt.attempt_index,
                                    CaseAttempt.status).where(CaseAttempt.run_id == uuid.UUID(run_id))).all()
        out: dict[tuple[str, int], tuple[int, str]] = {}
        for case_id, repeat, index, status in rows:
            prev = out.get((case_id, repeat))
            best = status if status in ("passed", "failed") else (prev[1] if prev else status)
            if prev and prev[1] in ("passed", "failed"):
                best = prev[1]
            out[(case_id, repeat)] = (max(index, prev[0] if prev else 0), best)
        return out

    def load_record(self, run_id: str) -> RunRecord:
        with self.session() as s:
            run = s.get(Run, uuid.UUID(run_id))
            if run is None:
                raise KeyError(run_id)
            attempts = s.scalars(select(CaseAttempt).where(CaseAttempt.run_id == run.id,
                                                           CaseAttempt.result.is_not(None))).all()
            trials = []
            for a in attempts:
                events = s.scalars(select(TraceEventRow).where(TraceEventRow.case_attempt_id == a.id)
                                   .order_by(TraceEventRow.sequence)).all()
                trials.append(TrialResult.model_validate({
                    **(a.result or {}),
                    "events": [_event_dict(e) for e in events],
                }))
            return RunRecord(str(run.id), run.side, run.manifest, trials, run.status)

    def import_record(self, record: RunRecord, content_sha256: str) -> str:
        """Idempotent import: identical content is a no-op; conflicts are rejected."""
        rid = uuid.UUID(record.run_id)
        with self.session() as s:
            existing = s.get(Run, rid)
            if existing is not None:
                if existing.content_sha256 == content_sha256:
                    return "unchanged"
                raise ValueError(f"run {record.run_id} already exists with different content")
            s.add(Run(id=rid, dataset_version_id=self._dataset_id(s, record.manifest),
                      agent_config_id=self._config_id(s, record.manifest), side=record.side,
                      mode=record.mode, status=record.status, manifest=record.manifest,
                      content_sha256=content_sha256, finished_at=datetime.now(UTC)))
        for trial in record.trials:
            attempt_id = uuid.uuid4()
            with self.session() as s:
                s.add(CaseAttempt(id=attempt_id, run_id=rid, case_id=trial.case_id, category=trial.category,
                                  repeat_index=trial.repeat_index, attempt_index=trial.attempt_index,
                                  seed=trial.seed, status="running"))
            self.finish_attempt(attempt_id, trial, persist_events=True)
        return "imported"

    def set_content_hash(self, run_id: str, content_sha256: str) -> None:
        with self.session() as s:
            s.execute(update(Run).where(Run.id == uuid.UUID(run_id)).values(content_sha256=content_sha256))

    def save_comparison(self, comparison_id: str, baseline_id: str, candidate_id: str,
                        gate: dict[str, Any], report: dict[str, Any], markdown: str) -> None:
        with self.session() as s:
            s.add(Comparison(id=uuid.UUID(comparison_id), baseline_run_id=uuid.UUID(baseline_id),
                             candidate_run_id=uuid.UUID(candidate_id), gate_sha256=gate["gate_sha256"],
                             status=gate["status"], report=report, report_markdown=markdown))

    def get_comparison(self, comparison_id: str) -> Comparison | None:
        with self.session() as s:
            return s.get(Comparison, uuid.UUID(comparison_id))

    def list_comparisons(self, limit: int = 25) -> list[Comparison]:
        with self.session() as s:
            return list(s.scalars(select(Comparison).order_by(Comparison.created_at.desc()).limit(limit)).all())

    def attempt_ids(self, run_id: str) -> dict[tuple[str, int], str]:
        with self.session() as s:
            rows = s.execute(select(CaseAttempt.case_id, CaseAttempt.repeat_index, CaseAttempt.id,
                                    CaseAttempt.attempt_index)
                             .where(CaseAttempt.run_id == uuid.UUID(run_id))
                             .order_by(CaseAttempt.attempt_index)).all()
        return {(c, r): str(i) for c, r, i, _ in rows}

    def add_judge_review(self, attempt_id: str, judge_config_sha256: str, rubric_sha256: str,
                         verdict: str, payload: dict[str, Any]) -> None:
        with self.session() as s:
            s.add(JudgeReview(case_attempt_id=uuid.UUID(attempt_id), judge_config_sha256=judge_config_sha256,
                              rubric_sha256=rubric_sha256, verdict=verdict, payload=payload))

    def review_queue(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.session() as s:
            rows = s.execute(select(JudgeReview, CaseAttempt).join(CaseAttempt)
                             .where(JudgeReview.verdict != "pass")
                             .order_by(JudgeReview.created_at.desc()).limit(limit)).all()
            return [{"attempt_id": str(a.id), "run_id": str(a.run_id), "case_id": a.case_id,
                     "repeat_index": a.repeat_index, "structured_status": a.status, "verdict": r.verdict,
                     "failure_types": r.payload.get("failure_types", []),
                     "explanation": r.payload.get("explanation", ""),
                     "human_review_status": r.human_review_status} for r, a in rows]


class DbRecorder:
    """TrialRecorder that persists events as they happen."""

    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self._current: uuid.UUID | None = None
        self.rerun_reasons: dict[tuple[str, int], str] = {}

    def begin(self, run: RunState, loaded: LoadedCase, repeat_index: int, seed: int,
              attempt_index: int) -> Callable[[TraceEvent], None]:
        reason = self.rerun_reasons.get((loaded.case.case_id, repeat_index))
        self._current = self.repo.begin_attempt(run.run_id, loaded, repeat_index, seed, attempt_index, reason)
        attempt_id = self._current
        return lambda event: self.repo.add_event(attempt_id, event)

    def finish(self, run: RunState, trial: TrialResult) -> None:
        assert self._current is not None
        self.repo.finish_attempt(self._current, trial)
        self._current = None


def _run_dict(run: Run, counts: dict[str, int]) -> dict[str, Any]:
    m = run.manifest
    planned = len(m["test"]["case_ids"]) * int(m["test"]["repeats"])
    elapsed = (run.finished_at - run.started_at).total_seconds() if run.finished_at else None
    return {
        "run_id": str(run.id), "side": run.side, "mode": run.mode, "status": run.status,
        "agent_id": m["agent"]["config"]["agent_id"], "model": m["agent"]["model"].get("tag"),
        "model_digest": m["agent"]["model"].get("digest"),
        "mutation_source": m["agent"].get("mutation_source"), "suite": m["test"]["suite"],
        "dataset_sha256": m["test"]["dataset_sha256"], "planned": planned,
        "passed": counts.get("passed", 0), "failed": counts.get("failed", 0),
        "infrastructure_errors": counts.get("infrastructure_error", 0),
        "abandoned": counts.get("abandoned", 0),
        "started_at": run.started_at.isoformat(), "elapsed_seconds": elapsed,
        "config_sha256": sha256_json(m["agent"]["config"])[:12],
    }


def _attempt_dict(a: CaseAttempt) -> dict[str, Any]:
    result = a.result or {}
    return {
        "attempt_id": str(a.id), "case_id": a.case_id, "category": a.category,
        "repeat_index": a.repeat_index, "attempt_index": a.attempt_index, "seed": a.seed,
        "status": a.status, "latency_ms": a.latency_ms, "rerun_reason": a.rerun_reason,
        "failed_checks": [c["code"] for c in result.get("checks", []) if c["status"] == "fail"],
        "semantic_review_status": result.get("semantic_review_status", "not_run"),
        "evaluation_mode": result.get("evaluation_mode"),
    }


def _event_dict(e: TraceEventRow) -> dict[str, Any]:
    return {"event_id": e.event_id, "sequence": e.sequence, "event_type": e.event_type,
            "payload": e.payload, "wall_time": e.wall_time, "elapsed_ms": e.elapsed_ms}
