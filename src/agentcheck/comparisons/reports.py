"""Report exporters: Markdown, JSON, JUnit XML, JSONL traces, review queue.

All model output, tool notes and imported text is untrusted. Markdown output
escapes HTML and table syntax; JUnit is produced with ElementTree, which
escapes XML.
"""

from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentcheck import SCHEMA_VERSION
from agentcheck.canonical import canonical_json, dumps_pretty, sha256_text
from agentcheck.comparisons.metrics import RunRecord, summarize
from agentcheck.runner.execution import latest_trials
from agentcheck.runner.manifests import agent_differences, performance_comparable
from agentcheck.schemas.results import TrialResult

RESULTS_SCHEMA = "agentcheck.results/v1"
RUN_EXPORT_SCHEMA = "agentcheck.run/v1"
MODE_BANNER = {
    "fixture": "**FIXTURE MODE**: scripted reference policy and controlled mutations. No language model was run.",
    "replay": "**REPLAY MODE**: saved traces re-scored without contacting a model.",
    "live_model": "**LIVE MODEL MODE**: genuine local inference through Ollama.",
}


def md(value: Any, limit: int = 300) -> str:
    """Escape untrusted text for a Markdown table cell or inline span."""
    text = value if isinstance(value, str) else canonical_json(value)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("|", "\\|").replace("`", "\\`").replace("*", "\\*").replace("_", "\\_")
    text = re.sub(r"[\r\n]+", " ", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def trace_name(trial: TrialResult) -> str:
    return f"{trial.case_id}__r{trial.repeat_index}__a{trial.attempt_index}.jsonl"


def run_export(record: RunRecord) -> dict[str, Any]:
    body = {
        "schema": RUN_EXPORT_SCHEMA,
        "run_id": record.run_id,
        "side": record.side,
        "status": record.status,
        "manifest": record.manifest,
        "trials": [t.model_dump(mode="json") for t in record.trials],
    }
    return {**body, "content_sha256": sha256_text(canonical_json(body))}


def write_run(record: RunRecord, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "run.json"
    path.write_text(json.dumps(run_export(record), ensure_ascii=False) + "\n", encoding="utf-8")
    traces = directory / "traces"
    traces.mkdir(exist_ok=True)
    for trial in record.trials:
        (traces / trace_name(trial)).write_text(
            "".join(json.dumps(e.model_dump(mode="json"), ensure_ascii=False) + "\n" for e in trial.events),
            encoding="utf-8",
        )
    return path


def read_run(path: Path) -> tuple[RunRecord, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != RUN_EXPORT_SCHEMA:
        raise ValueError(f"{path} is not an AgentCheck run export")
    claimed = data.pop("content_sha256", None)
    actual = sha256_text(canonical_json(data))
    if claimed != actual:
        raise ValueError(f"{path}: content hash mismatch (file edited or corrupted)")
    trials = [TrialResult.model_validate(t) for t in data["trials"]]
    return RunRecord(data["run_id"], data["side"], data["manifest"], trials, data["status"]), actual


def _suite(parent: ET.Element, name: str, trials: list[TrialResult]) -> None:
    failures = sum(t.status == "failed" for t in trials)
    errors = sum(t.status in ("infrastructure_error", "cancelled") for t in trials)
    suite = ET.SubElement(parent, "testsuite", name=name, tests=str(len(trials)),
                          failures=str(failures), errors=str(errors))
    for t in trials:
        case = ET.SubElement(suite, "testcase", classname=f"{name}.{t.category}",
                             name=f"{t.case_id}[r{t.repeat_index}]",
                             time=f"{t.metrics.latency_ms / 1000:.3f}")
        if t.status == "failed":
            lines = [f"{c.code}: {c.message} | expected={canonical_json(c.expected)[:300]} "
                     f"actual={canonical_json(c.actual)[:300]}" for c in t.failed_checks]
            node = ET.SubElement(case, "failure", message=", ".join(c.code for c in t.failed_checks),
                                 type="check_failure")
            node.text = "\n".join(lines)
        elif t.status in ("infrastructure_error", "cancelled"):
            node = ET.SubElement(case, "error", message=t.status, type=t.status)
            node.text = t.error or ""


def junit_xml(suites: list[tuple[str, list[TrialResult]]], gate: dict[str, Any] | None = None) -> str:
    root = ET.Element("testsuites", name="agentcheck")
    for name, trials in suites:
        _suite(root, name, trials)
    if gate is not None:
        suite = ET.SubElement(root, "testsuite", name="gate", tests="1",
                              failures=str(int(gate["status"] == "FAIL")),
                              errors=str(int(gate["status"] == "INCONCLUSIVE")))
        case = ET.SubElement(suite, "testcase", classname="gate", name="deterministic_comparison")
        if gate["status"] != "PASS":
            tag = "failure" if gate["status"] == "FAIL" else "error"
            node = ET.SubElement(case, tag, message=f"gate {gate['status']}", type=gate["status"])
            node.text = "\n".join(f"{r['rule']}: {r['detail']}" for r in gate["rules"] if r["triggered"])
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def write_review_queue(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["side", "run_id", "case_id", "repeat_index", "structured_pass",
              "semantic_review_status", "verdict", "failure_types", "explanation"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def review_rows(record: RunRecord, judge: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = []
    for t in latest_trials(record.trials).values():
        verdict = (judge or {}).get(f"{t.case_id}:{t.repeat_index}")
        if t.semantic_review_status in ("advisory_fail", "needs_review") or (
            verdict and verdict.get("verdict") != "pass"
        ):
            rows.append({
                "side": record.side, "run_id": record.run_id, "case_id": t.case_id,
                "repeat_index": t.repeat_index, "structured_pass": t.structured_pass,
                "semantic_review_status": t.semantic_review_status,
                "verdict": (verdict or {}).get("verdict", ""),
                "failure_types": ";".join((verdict or {}).get("failure_types", [])),
                "explanation": (verdict or {}).get("explanation", ""),
            })
    return rows


def _semantic_line(summary: dict[str, Any]) -> str:
    dist = summary["semantic_review"]
    if set(dist) <= {"not_run"}:
        return ("Semantic review: **not run**. Natural-language accuracy of the `message` field "
                "was not evaluated; a PASS here covers structured outputs and traces only.")
    parts = ", ".join(f"{k}: {v}" for k, v in sorted(dist.items()))
    return f"Semantic review (advisory, never gates): {parts}."


def _summary_rows(summaries: list[dict[str, Any]]) -> list[str]:
    rows = ["| | " + " | ".join(s["side"] for s in summaries) + " |",
            "| --- | " + " | ".join("---:" for _ in summaries) + " |"]
    fields: list[tuple[str, Callable[[dict[str, Any]], Any]]] = [
        ("Planned trials", lambda s: s["planned"]),
        ("Completed (scored)", lambda s: s["completed"]),
        ("Passed", lambda s: s["passed"]),
        ("Failed", lambda s: s["failed"]),
        ("Infrastructure errors", lambda s: s["infrastructure_errors"]),
        ("Missing / cancelled", lambda s: s["missing"] + s["cancelled"]),
        ("Structured pass rate (release)", lambda s: pct(s["structured_pass_rate"])
         if s["complete"] else "INCOMPLETE"),
        ("Completed-trial pass rate (diagnostic)", lambda s: pct(s["completed_trial_pass_rate"])),
        ("Consistency (cases passing every repeat)", lambda s:
         f"{s['consistency']['cases_passing_all_repeats']}/{s['consistency']['cases']}"),
        ("First-attempt valid format", lambda s:
         f"{s['protocol']['first_attempt_valid']}/{s['protocol']['first_attempt_total']}"),
        ("Format repairs", lambda s: s["protocol"]["repairs"]),
        ("Forbidden tool attempts / executions", lambda s:
         f"{s['safety']['forbidden_tool_attempts']} / {s['safety']['forbidden_tool_executions']}"),
        ("Disclosure failures", lambda s: s["safety"]["disclosure_failures"]),
        ("Fabricated-action failures", lambda s: s["safety"]["fabricated_action_failures"]),
        ("Model calls per trial", lambda s: _num(s["usage"]["model_calls_per_trial"])),
        ("Tool attempts per trial", lambda s: _num(s["usage"]["tool_attempts_per_trial"])),
        ("Latency median / p95 (ms)", lambda s:
         f"{_num(s['usage']['latency_ms_median'])} / {_num(s['usage']['latency_ms_p95'])}"),
        ("Prompt / completion tokens", lambda s:
         f"{s['usage']['prompt_tokens_total'] or 'N/A'} / {s['usage']['completion_tokens_total'] or 'N/A'}"),
    ]
    for label, fn in fields:
        rows.append(f"| {label} | " + " | ".join(str(fn(s)) for s in summaries) + " |")
    return rows


def _num(value: float | None) -> str:
    return "N/A" if value is None else f"{value:,.1f}"


def _model_line(manifest: dict[str, Any]) -> str:
    model = manifest["agent"]["model"]
    digest = model.get("digest")
    return (f"**{md(model.get('tag'))}**" + (f", digest `{digest}`" if digest else " (no weights)")
            + (f", {model.get('parameter_size')} {model.get('quantization_level')}"
               if model.get("parameter_size") else ""))


def comparison_markdown(
    baseline: RunRecord, candidate: RunRecord, comparison: dict[str, Any], gate: dict[str, Any],
    compat: list[str], trace_prefix: str = "traces",
) -> str:
    mb, mc = baseline.manifest, candidate.manifest
    mode = mc["test"]["evaluation_mode"]
    env = mc["environment"]
    perf_ok, perf_reasons = performance_comparable(mb, mc)
    lines = [
        "# AgentCheck comparison report", "",
        f"> {MODE_BANNER.get(mode, mode)}", "",
        f"## Gate: {gate['status']} (exit {gate['exit_code']})", "",
        _semantic_line(comparison["candidate"]), "",
        f"Suite **{md(mc['test']['suite'])}**: {len(mc['test']['case_ids'])} cases x "
        f"{mc['test']['repeats']} repeat(s) (seeds {mc['test']['seeds']}) x 2 configurations. "
        f"Dataset `{mc['test']['dataset_sha256'][:16]}`. Gate `{gate['gate']['name']}` "
        f"(`{gate['gate_sha256'][:16]}`).", "",
        f"Pass-rate delta (candidate - baseline): **{comparison['pass_rate_delta_pp']} pp**. "
        f"Regressions {comparison['counts']['regressions']}, improvements "
        f"{comparison['counts']['improvements']}, unchanged pass "
        f"{comparison['counts']['unchanged_pass']}, unchanged fail {comparison['counts']['unchanged_fail']}, "
        f"incomplete {comparison['counts']['incomplete']}.", "",
        "## Configuration changes", "",
    ]
    diffs = agent_differences(mb, mc)
    if diffs:
        lines += ["| Field | Baseline | Candidate |", "| --- | --- | --- |"]
        lines += [f"| {md(d['field'])} | {md(d['baseline'])} | {md(d['candidate'])} |" for d in diffs]
    else:
        lines.append("No behavioural difference: this is a stability check.")
    if compat:
        lines += ["", "**Incompatible manifests:**", *[f"- {md(p)}" for p in compat]]

    lines += ["", "## Gate rules", "", "| Rule | Result | Detail |", "| --- | --- | --- |"]
    lines += [f"| {r['rule']} | {r['outcome']} | {md(r['detail'])} |" for r in gate["rules"]]
    if gate["smoke_rules"]:
        lines += ["", "_One repeat: regression rules use smoke semantics (1/1 vs 0/1). "
                  "This is a fast engineering check, not a stochastic estimate._"]

    lines += ["", "## Coverage and metrics", "", *_summary_rows([comparison["baseline"], comparison["candidate"]])]

    lines += ["", "## Results by category", "", "| Category | Baseline | Candidate |", "| --- | ---: | ---: |"]
    cats = sorted(set(comparison["baseline"]["per_category"]) | set(comparison["candidate"]["per_category"]))
    for cat in cats:
        b = comparison["baseline"]["per_category"].get(cat, {"passed": 0, "total": 0})
        c = comparison["candidate"]["per_category"].get(cat, {"passed": 0, "total": 0})
        lines.append(f"| {cat} | {b['passed']}/{b['total']} | {c['passed']}/{c['total']} |")

    lines += ["", "## Regressions", ""]
    if not comparison["regressed_cases"]:
        lines.append("None observed.")
    for case in comparison["regressed_cases"]:
        r = case.get("repeat", 1)
        trace = f"{trace_prefix}/candidate/{case['case_id']}__r{r}__a1.jsonl"
        tag = " (consistent)" if case["consistent_regression"] else ""
        lines += [
            f"### `{case['case_id']}`{tag}", "",
            f"Category `{case['category']}`. Baseline {case['baseline_passed']}/{case['repeats']}, "
            f"candidate {case['candidate_passed']}/{case['repeats']}. Candidate trace: [{trace}]({trace})", "",
            "| Check | Expected | Actual | Message |", "| --- | --- | --- | --- |",
        ]
        for chk in case.get("failed_checks", []):
            lines.append(f"| {chk['code']} ({chk['severity']}) | {md(chk['expected'], 160)} | "
                         f"{md(chk['actual'], 160)} | {md(chk['message'], 220)} |")
        div = case.get("first_divergence")
        if div:
            lines += ["", f"First divergence at trace position {div['index']}:", "",
                      f"- baseline: **{md((div['baseline'] or {}).get('event_type'))}** "
                      f"{md((div['baseline'] or {}).get('payload'), 260)}",
                      f"- candidate: **{md((div['candidate'] or {}).get('event_type'))}** "
                      f"{md((div['candidate'] or {}).get('payload'), 260)}"]
        lines.append("")

    improvements = [c for c in comparison["cases"] if c["classification"] == "improvement"]
    if improvements:
        lines += ["## Improvements", ""]
        lines += [f"- `{c['case_id']}`: {c['baseline_passed']}/{c['repeats']} -> "
                  f"{c['candidate_passed']}/{c['repeats']}" for c in improvements]
        lines.append("")

    lines += [
        "## Environment", "",
        f"- Baseline model: {_model_line(mb)}",
        f"- Candidate model: {_model_line(mc)}",
        f"- Hardware: {md(env.get('cpu'))}, {round((env.get('ram_bytes') or 0) / 2**30)} GiB RAM, "
        f"{md(env.get('accelerator'))}; {md(env.get('platform'))}",
        f"- Ollama {env.get('ollama_version') or 'not used'}; Python {env.get('python')}; "
        f"git `{(env.get('git_commit') or 'none')[:12]}`{' (dirty)' if env.get('git_dirty') else ''}",
        "- API spend: $0.00. Energy and hardware cost: unknown.",
        f"- Performance comparable: {'yes' if perf_ok else 'no: ' + md('; '.join(perf_reasons))}",
        "",
        "## Limitations", "",
        "- Deterministic checks cover structured fields and traces, not every sentence of `message`.",
        "- Small, single-domain synthetic suite; not a general model benchmark.",
        "- Temperature 0 and fixed seeds reduce but do not eliminate run-to-run variation.",
    ]
    if mode == "fixture":
        lines.append("- Fixture mode demonstrates the evaluation platform. Mutations are controlled "
                     "fault injections, not observed model behaviour.")
    return "\n".join(lines) + "\n"


def run_markdown(record: RunRecord) -> str:
    s = summarize(record)
    m = record.manifest
    lines = [
        f"# AgentCheck run `{record.run_id}`", "",
        f"> {MODE_BANNER.get(record.mode, record.mode)}", "",
        f"Agent **{md(m['agent']['config']['agent_id'])}**, model {_model_line(m)}, suite "
        f"**{md(m['test']['suite'])}**, status **{record.status}**.", "",
        _semantic_line(s), "",
        *_summary_rows([s]), "",
        "## Failed trials", "",
    ]
    failed = [t for t in latest_trials(record.trials).values() if t.status != "passed"]
    if not failed:
        lines.append("None.")
    for t in failed:
        lines.append(f"- `{t.case_id}` r{t.repeat_index} **{t.status}**: "
                     + md("; ".join(f"{c.code}: {c.message}" for c in t.failed_checks) or (t.error or ""), 400))
    return "\n".join(lines) + "\n"


def write_comparison(
    output: Path, baseline: RunRecord, candidate: RunRecord, comparison: dict[str, Any],
    gate: dict[str, Any], compat: list[str], comparison_id: str,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    for record in (baseline, candidate):
        write_run(record, output / "runs" / record.side)
        tdir = output / "traces" / record.side
        tdir.mkdir(parents=True, exist_ok=True)
        for trial in record.trials:
            (tdir / trace_name(trial)).write_text(
                "".join(json.dumps(e.model_dump(mode="json"), ensure_ascii=False) + "\n" for e in trial.events),
                encoding="utf-8",
            )
    paths = {
        "report": output / "report.md",
        "results": output / "results.json",
        "junit": output / "junit.xml",
        "manifest": output / "manifest.json",
        "review_queue": output / "review_queue.csv",
    }
    paths["report"].write_text(comparison_markdown(baseline, candidate, comparison, gate, compat), "utf-8")
    results = {
        "schema": RESULTS_SCHEMA, "schema_version": SCHEMA_VERSION, "comparison_id": comparison_id,
        "evaluation_mode": candidate.mode, "gate": gate, "compatibility_problems": compat,
        "baseline_run_id": baseline.run_id, "candidate_run_id": candidate.run_id,
        "comparison": comparison,
        "per_trial": {
            rec.side: [{"case_id": t.case_id, "repeat_index": t.repeat_index, "attempt_index": t.attempt_index,
                        "status": t.status, "structured_pass": t.structured_pass,
                        "semantic_review_status": t.semantic_review_status,
                        "mutation_source": t.mutation_source,
                        "failed_checks": [c.code for c in t.failed_checks]} for t in rec.trials]
            for rec in (baseline, candidate)
        },
    }
    paths["results"].write_text(dumps_pretty(results), "utf-8")
    suites = [("baseline", baseline.trials), ("candidate", candidate.trials)]
    paths["junit"].write_text(junit_xml(suites, gate), "utf-8")
    paths["manifest"].write_text(dumps_pretty({
        "comparison_id": comparison_id, "baseline": baseline.manifest, "candidate": candidate.manifest,
        "gate": gate["gate"], "gate_sha256": gate["gate_sha256"],
    }), "utf-8")
    write_review_queue(paths["review_queue"], review_rows(baseline) + review_rows(candidate))
    return paths


def write_single_run(output: Path, record: RunRecord) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    write_run(record, output)
    paths = {"report": output / "report.md", "results": output / "results.json",
             "junit": output / "junit.xml", "manifest": output / "manifest.json",
             "review_queue": output / "review_queue.csv"}
    paths["report"].write_text(run_markdown(record), "utf-8")
    paths["results"].write_text(dumps_pretty({
        "schema": RESULTS_SCHEMA, "schema_version": SCHEMA_VERSION, "run_id": record.run_id,
        "evaluation_mode": record.mode, "summary": summarize(record),
    }), "utf-8")
    paths["junit"].write_text(junit_xml([(record.side, record.trials)]), "utf-8")
    paths["manifest"].write_text(dumps_pretty(record.manifest), "utf-8")
    write_review_queue(paths["review_queue"], review_rows(record))
    return paths
