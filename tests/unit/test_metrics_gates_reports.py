from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from agentcheck.comparisons.gates import GateConfig, evaluate_gate
from agentcheck.comparisons.metrics import RunRecord, compare, summarize
from agentcheck.comparisons.reports import junit_xml, md, read_run, write_run
from agentcheck.runner.manifests import compatibility_problems
from agentcheck.schemas.results import CheckResult, TrialMetrics, TrialResult

GATE = GateConfig(name="test")


def trial(case_id: str, repeat: int, status: str, failed: tuple[str, ...] = (), **metrics: Any) -> TrialResult:
    checks = [CheckResult(code=c, status="fail", severity="error") for c in failed]
    return TrialResult(case_id=case_id, category="cat", split="dev", scenario_family_id="f", repeat_index=repeat,
                       seed=41 + repeat, status=status, structured_pass=status == "passed",  # type: ignore[arg-type]
                       evaluation_mode="fixture", checks=checks, metrics=TrialMetrics(**metrics), events=[])


def manifest(cases: list[str], repeats: int = 1) -> dict[str, Any]:
    return {"test": {"case_ids": cases, "repeats": repeats, "seeds": [42, 43, 44][:repeats],
                     "evaluation_mode": "fixture", "dataset_sha256": "d", "case_ids_sha256": "c",
                     "worlds_sha256": "w", "policy_version": "p", "policy_sha256": "ps", "oracle_sha256": "o",
                     "scorer_version": "s", "scorer_sha256": "ss", "protocol_version": "v",
                     "dispatcher_sha256": "x", "suite": "t"},
            "agent": {"config": {"agent_id": "a"}, "model": {"tag": "m"}}, "environment": {}}


def record(side: str, trials: list[TrialResult], cases: list[str], repeats: int = 1) -> RunRecord:
    return RunRecord(f"{side}-id", side, manifest(cases, repeats), trials)


def gate_for(b: RunRecord, c: RunRecord, compat: list[str] | None = None) -> dict[str, Any]:
    return evaluate_gate(compare(b, c), GATE, compat or [])


def test_incomplete_run_has_no_release_rate_and_is_inconclusive() -> None:
    b = record("baseline", [trial("a", 1, "passed"), trial("b", 1, "passed")], ["a", "b"])
    c = record("candidate", [trial("a", 1, "passed"), trial("b", 1, "infrastructure_error")], ["a", "b"])
    s = summarize(c)
    assert s["structured_pass_rate"] is None and s["completed_trial_pass_rate"] == 1.0
    assert s["infrastructure_errors"] == 1 and s["planned"] == 2
    g = gate_for(b, c)
    assert g["status"] == "INCONCLUSIVE" and g["exit_code"] == 2


def test_missing_trials_are_counted() -> None:
    c = record("candidate", [trial("a", 1, "passed")], ["a", "b"])
    s = summarize(c)
    assert s["missing"] == 1 and not s["complete"]


def test_inconclusive_takes_precedence_over_fail() -> None:
    b = record("baseline", [trial("a", 1, "passed")], ["a"])
    c = record("candidate", [trial("a", 1, "failed", ("ownership",))], ["a"])
    assert gate_for(b, c, ["dataset differs"])["status"] == "INCONCLUSIVE"


def test_disclosure_fails_even_with_better_pass_rate() -> None:
    cases = [f"c{i}" for i in range(20)]
    b = record("baseline", [trial(x, 1, "failed", ("task_outcome",)) for x in cases], cases)
    c = record("candidate", [trial(x, 1, "passed") for x in cases[:-1]] +
               [trial(cases[-1], 1, "failed", ("ownership",))], cases)
    g = gate_for(b, c)
    assert g["status"] == "FAIL"
    assert next(r for r in g["rules"] if r["rule"] == "candidate_disclosure")["triggered"]


def test_forbidden_tool_attempt_fails() -> None:
    b = record("baseline", [trial("a", 1, "passed")], ["a"])
    c = record("candidate", [trial("a", 1, "failed", ("tool_allowlist",), forbidden_tool_attempts=1)], ["a"])
    assert gate_for(b, c)["status"] == "FAIL"


def test_consistent_regression_3_of_3_to_0_of_3() -> None:
    cases = [f"c{i}" for i in range(40)]
    b = record("baseline", [trial(x, r, "passed") for x in cases for r in (1, 2, 3)], cases, 3)
    c_trials = [trial(x, r, "passed") for x in cases[1:] for r in (1, 2, 3)]
    c_trials += [trial("c0", r, "failed", ("task_outcome",)) for r in (1, 2, 3)]
    c = record("candidate", c_trials, cases, 3)
    comp = compare(b, c)
    assert comp["pass_rate_delta_pp"] == -2.5  # under the 5 pp threshold...
    g = evaluate_gate(comp, GATE, [])
    assert g["status"] == "FAIL"  # ...but the consistent regression still fails the gate
    assert not g["smoke_rules"]


def test_partial_regression_within_threshold_passes() -> None:
    cases = [f"c{i}" for i in range(40)]
    b = record("baseline", [trial(x, r, "passed") for x in cases for r in (1, 2, 3)], cases, 3)
    c_trials = [trial(x, r, "passed") for x in cases[1:] for r in (1, 2, 3)]
    c_trials += [trial("c0", 1, "failed", ("task_outcome",)), trial("c0", 2, "passed"), trial("c0", 3, "passed")]
    comp = compare(b, record("candidate", c_trials, cases, 3))
    assert comp["counts"]["regressions"] == 1
    assert evaluate_gate(comp, GATE, [])["status"] == "PASS"


def test_pass_rate_drop_and_protocol_increase() -> None:
    cases = [f"c{i}" for i in range(10)]
    b = record("baseline", [trial(x, 1, "failed", ("task_outcome",)) if i < 1 else trial(x, 1, "passed")
                            for i, x in enumerate(cases)], cases)
    c = record("candidate", [trial(x, 1, "failed", ("protocol_valid",)) if i < 3 else trial(x, 1, "passed")
                             for i, x in enumerate(cases)], cases)
    g = gate_for(b, c)
    triggered = {r["rule"] for r in g["rules"] if r["triggered"]}
    assert {"pass_rate_drop", "protocol_failure_increase"} <= triggered


def test_denominators_report_na() -> None:
    s = summarize(record("x", [], ["a"]))
    assert s["protocol"]["first_attempt_valid_rate"] is None
    assert s["usage"]["latency_ms_median"] is None


def test_manifest_mismatch_detected() -> None:
    a, b = manifest(["a"]), copy.deepcopy(manifest(["a"]))
    b["test"]["scorer_sha256"] = "different"
    b["test"]["seeds"] = [1]
    problems = compatibility_problems(a, b)
    assert any("scorer_sha256" in p for p in problems) and any("seeds" in p for p in problems)


@pytest.mark.parametrize("payload", ["<script>alert(1)</script>", "a|b`c`*d*", "line\nbreak"])
def test_markdown_escapes_untrusted_text(payload: str) -> None:
    out = md(payload)
    assert "<script>" not in out and "\n" not in out
    assert "|" not in out.replace("\\|", "")


def test_junit_escapes_and_parses() -> None:
    t = trial("x", 1, "failed", ("required_fields",))
    t.checks[0].message = '<script>"&</script>'
    xml = junit_xml([("candidate", [t])])
    root = ET.fromstring(xml.split("\n", 1)[1])
    failure = root.find(".//failure")
    assert failure is not None and "<script>" in (failure.text or "")  # escaped in XML, intact after parse


def test_run_export_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    rec = record("baseline", [trial("a", 1, "passed")], ["a"])
    path = write_run(rec, tmp_path)
    loaded, digest = read_run(path)
    assert loaded.run_id == rec.run_id and len(digest) == 64
    path.write_text(path.read_text().replace('"passed"', '"failed"', 1))
    with pytest.raises(ValueError, match="hash mismatch"):
        read_run(path)
