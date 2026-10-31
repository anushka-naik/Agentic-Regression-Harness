"""Run summaries and baseline/candidate comparisons.

Denominators are always explicit. A release pass rate exists only when the run
is complete and free of infrastructure errors; otherwise it is ``None`` and a
separately labelled completed-trial rate is reported for diagnosis.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from agentcheck.runner.execution import RunState, latest_trials
from agentcheck.schemas.results import TraceEvent, TrialResult

VOLATILE_KEYS = frozenset({
    "latency_ms", "inference_ms", "duration_ms", "metadata", "prompt_tokens", "completion_tokens",
    "estimated_prompt_tokens", "agent_id", "mutation_source", "mutation", "messages",
})


@dataclass
class RunRecord:
    run_id: str
    side: str
    manifest: dict[str, Any]
    trials: list[TrialResult] = field(default_factory=list)
    status: str = "completed"

    @property
    def case_ids(self) -> list[str]:
        return list(self.manifest["test"]["case_ids"])

    @property
    def repeats(self) -> int:
        return int(self.manifest["test"]["repeats"])

    @property
    def planned(self) -> int:
        return len(self.case_ids) * self.repeats

    @property
    def mode(self) -> str:
        return str(self.manifest["test"]["evaluation_mode"])

    @classmethod
    def from_state(cls, state: RunState) -> RunRecord:
        return cls(state.run_id, state.side, state.manifest, list(state.trials), state.status)


def ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(q * (len(ordered) - 1))))
    return ordered[index]


def _check_failed(trial: TrialResult, code: str) -> bool:
    return any(c.code == code and c.status == "fail" for c in trial.checks)


def summarize(run: RunRecord) -> dict[str, Any]:
    latest = latest_trials(run.trials)
    scored = [t for t in latest.values() if t.status in ("passed", "failed")]
    passed = [t for t in scored if t.status == "passed"]
    infra = [t for t in latest.values() if t.status == "infrastructure_error"]
    cancelled = [t for t in latest.values() if t.status == "cancelled"]
    missing = run.planned - len(latest)
    complete = missing == 0 and not infra and not cancelled

    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "total": 0})
    for t in scored:
        categories[t.category]["total"] += 1
        categories[t.category]["passed"] += t.status == "passed"

    per_case: dict[str, list[TrialResult]] = defaultdict(list)
    for t in scored:
        per_case[t.case_id].append(t)
    consistent = sum(
        1 for trials in per_case.values()
        if len(trials) == run.repeats and all(t.status == "passed" for t in trials)
    )

    first_total = sum(t.metrics.first_attempt_total for t in scored)
    first_valid = sum(t.metrics.first_attempt_valid for t in scored)
    latencies = [t.metrics.latency_ms for t in scored]
    prompt_tokens = [t.metrics.prompt_tokens for t in scored if t.metrics.prompt_tokens is not None]
    completion = [t.metrics.completion_tokens for t in scored if t.metrics.completion_tokens is not None]
    semantic: dict[str, int] = defaultdict(int)
    for t in scored:
        semantic[t.semantic_review_status] += 1

    return {
        "run_id": run.run_id,
        "side": run.side,
        "evaluation_mode": run.mode,
        "status": run.status,
        "planned": run.planned,
        "completed": len(scored),
        "passed": len(passed),
        "failed": len(scored) - len(passed),
        "infrastructure_errors": len(infra),
        "cancelled": len(cancelled),
        "missing": missing,
        "complete": complete,
        "structured_pass_rate": ratio(len(passed), run.planned) if complete else None,
        "completed_trial_pass_rate": ratio(len(passed), len(scored)),
        "per_category": {
            k: {**v, "rate": ratio(v["passed"], v["total"])} for k, v in sorted(categories.items())
        },
        "consistency": {"cases_passing_all_repeats": consistent, "cases": len(run.case_ids),
                        "rate": ratio(consistent, len(run.case_ids))},
        "protocol": {
            "first_attempt_valid": first_valid, "first_attempt_total": first_total,
            "first_attempt_valid_rate": ratio(first_valid, first_total),
            "repairs": sum(t.metrics.repairs for t in scored),
            "protocol_failure_trials": sum(_check_failed(t, "protocol_valid") for t in scored),
            "protocol_failure_rate": ratio(sum(_check_failed(t, "protocol_valid") for t in scored), len(scored)),
        },
        "safety": {
            "forbidden_tool_attempts": sum(t.metrics.forbidden_tool_attempts for t in scored),
            "forbidden_tool_executions": sum(t.metrics.forbidden_tool_executions for t in scored),
            "disclosure_failures": sum(_check_failed(t, "ownership") for t in scored),
            "fabricated_action_failures": sum(_check_failed(t, "no_fabricated_action") for t in scored),
            "allowlist_failures": sum(_check_failed(t, "tool_allowlist") for t in scored),
        },
        "usage": {
            "model_calls_per_trial": ratio(sum(t.metrics.model_calls for t in scored), len(scored)),
            "tool_attempts_per_trial": ratio(sum(t.metrics.tool_attempts for t in scored), len(scored)),
            "latency_ms_median": statistics.median(latencies) if latencies else None,
            "latency_ms_p95": percentile(latencies, 0.95),
            "prompt_tokens_total": sum(prompt_tokens) if prompt_tokens else None,
            "completion_tokens_total": sum(completion) if completion else None,
            "api_spend_usd": 0.0,
            "energy": "unknown",
        },
        "semantic_review": dict(semantic),
    }


def _normalize(event: TraceEvent) -> tuple[str, Any]:
    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in VOLATILE_KEYS}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return event.event_type, strip(event.payload)


def _event_summary(event: TraceEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {"event_id": event.event_id, "event_type": event.event_type, "payload": _normalize(event)[1]}


def first_divergence(a: TrialResult, b: TrialResult) -> dict[str, Any] | None:
    """First trace event where two trials of the same case differ (timings ignored)."""
    ea = [e for e in a.events if e.event_type not in ("case_started", "check_result")]
    eb = [e for e in b.events if e.event_type not in ("case_started", "check_result")]
    for i in range(max(len(ea), len(eb))):
        x = ea[i] if i < len(ea) else None
        y = eb[i] if i < len(eb) else None
        if x is None or y is None or _normalize(x) != _normalize(y):
            return {
                "index": i,
                "baseline": _event_summary(x),
                "candidate": _event_summary(y),
            }
    return None


def compare(baseline: RunRecord, candidate: RunRecord) -> dict[str, Any]:
    lb, lc = latest_trials(baseline.trials), latest_trials(candidate.trials)
    n = baseline.repeats
    cases: list[dict[str, Any]] = []
    counts = {"regressions": 0, "improvements": 0, "unchanged_pass": 0, "unchanged_fail": 0,
              "incomplete": 0}
    for case_id in baseline.case_ids:
        bt = [lb.get((case_id, r)) for r in range(1, n + 1)]
        ct = [lc.get((case_id, r)) for r in range(1, n + 1)]
        scored = all(t is not None and t.status in ("passed", "failed") for t in bt + ct)
        bp = sum(1 for t in bt if t is not None and t.status == "passed")
        cp = sum(1 for t in ct if t is not None and t.status == "passed")
        if not scored:
            kind = "incomplete"
        elif cp < bp:
            kind = "regression"
        elif cp > bp:
            kind = "improvement"
        elif bp == n:
            kind = "unchanged_pass"
        else:
            kind = "unchanged_fail"
        counts["regressions" if kind == "regression" else
               "improvements" if kind == "improvement" else kind] += 1
        entry: dict[str, Any] = {
            "case_id": case_id,
            "category": next((t.category for t in bt + ct if t is not None), None),
            "baseline_passed": bp, "candidate_passed": cp, "repeats": n,
            "classification": kind,
            "consistent_regression": kind == "regression" and bp == n and cp == 0,
        }
        if kind in ("regression", "improvement"):
            for r in range(1, n + 1):
                b_trial, c_trial = bt[r - 1], ct[r - 1]
                if b_trial and c_trial and b_trial.status != c_trial.status:
                    worse = c_trial if kind == "regression" else b_trial
                    entry["repeat"] = r
                    entry["first_divergence"] = first_divergence(b_trial, c_trial)
                    entry["failed_checks"] = [
                        {"code": c.code, "severity": c.severity, "expected": c.expected,
                         "actual": c.actual, "message": c.message, "event_ids": c.event_ids}
                        for c in worse.failed_checks
                    ]
                    break
        cases.append(entry)

    sb, sc = summarize(baseline), summarize(candidate)
    rate_b, rate_c = sb["structured_pass_rate"], sc["structured_pass_rate"]
    return {
        "baseline": sb,
        "candidate": sc,
        "pass_rate_delta_pp": None if rate_b is None or rate_c is None else round((rate_c - rate_b) * 100, 2),
        "counts": counts,
        "cases": cases,
        "regressed_cases": [c for c in cases if c["classification"] == "regression"],
    }
