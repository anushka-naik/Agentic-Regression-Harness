"""AgentCheck command-line interface.

Exit codes: 0 success / deterministic comparison passed; 1 evaluated regression
or check failure; 2 invalid configuration, incompatible manifests, unavailable
infrastructure or incomplete evaluation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import traceback
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated, Any

import typer

from agentcheck.errors import AgentCheckError
from agentcheck.settings import Settings

app = typer.Typer(no_args_is_help=True, add_completion=False, rich_markup_mode=None,
                  help="Reproducible regression evaluation for tool-using support agents.")
dataset_app = typer.Typer(no_args_is_help=True, help="Dataset commands.")
app.add_typer(dataset_app, name="dataset")

STATE: dict[str, Any] = {"debug": False}
log = logging.getLogger("agentcheck")

SuiteOpt = Annotated[str, typer.Option(help="smoke | dev | validation | heldout | all")]
OutputOpt = Annotated[Path | None, typer.Option(help="Output directory (default: under ARTIFACTS_DIR)")]
GateOpt = Annotated[Path, typer.Option(help="Gate policy YAML")]
DbOpt = Annotated[
    bool | None, typer.Option("--db/--no-db", help="Persist to PostgreSQL (default: if DATABASE_URL is set)")
]
ModeOpt = Annotated[str | None, typer.Option(help="fixture (scripted, no LLM) | live_model (default from config)")]


def echo(message: str) -> None:
    typer.echo(message)


def handle_errors[F: Callable[..., Any]](func: F) -> F:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except AgentCheckError as exc:
            if STATE["debug"]:
                traceback.print_exc()
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(2) from None
        except KeyboardInterrupt:
            typer.echo("interrupted: run left incomplete; use `agentcheck resume <run_id>`", err=True)
            raise typer.Exit(2) from None
    return wrapper  # type: ignore[return-value]


def settings() -> Settings:
    return Settings()


def workspace(use_db: bool | None) -> Any:
    from agentcheck.workflows import open_workspace

    return open_workspace(settings(), use_db, echo)


@app.callback()
def main(debug: Annotated[bool, typer.Option("--debug", help="Show tracebacks and debug logs")] = False) -> None:
    STATE["debug"] = debug
    logging.basicConfig(level=logging.DEBUG if debug else logging.WARNING, stream=sys.stderr)


@app.command()
@handle_errors
def doctor(model: Annotated[str, typer.Option(help="Ollama model tag to verify")] = "qwen3:4b-q4_K_M") -> None:
    """Check Ollama connectivity, model install and digest, JSON schema output and thinking=off."""
    from agentcheck.model_clients.base import ModelConfig
    from agentcheck.model_clients.ollama import OllamaModelClient
    from agentcheck.schemas.protocol import AgentAction, action_json_schema

    s = settings()
    client = OllamaModelClient(s.ollama_base_url)

    async def run() -> int:
        version = await client.version()
        echo(f"ok   Ollama {version} at {s.ollama_base_url}")
        fp = await client.model_fingerprint(model)
        echo(f"ok   {model} installed: digest {fp['digest']} ({fp['parameter_size']}, {fp['quantization_level']})")
        started = time.monotonic()
        prompt = s.resolve("prompts/support_v1.txt").read_text("utf-8")
        messages: list[dict[str, object]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"session": {"authenticated": True}, "conversation": [
                {"type": "user_message", "text": "What is the status of O-1006?"}]})},
        ]
        response = await client.next_action(messages, action_json_schema(),
                                            ModelConfig(model=model, num_predict=256, seed=42))
        elapsed = time.monotonic() - started
        try:
            parsed = AgentAction.model_validate_json(response.content)
            echo(f"ok   structured action parsed and validated ({elapsed:.1f}s, "
                 f"{response.completion_tokens} tokens): {parsed.action} {parsed.tool_name or ''}")
        except Exception as exc:
            echo(f"FAIL response did not validate against the action protocol: {exc}")
            return 2
        if response.metadata.get("thinking_present"):
            echo("FAIL thinking output present although think=false was sent")
            return 2
        echo("ok   thinking disabled (no thinking field returned)")
        return 0

    raise typer.Exit(asyncio.run(run()))


@dataset_app.command("validate")
@handle_errors
def dataset_validate(path: Annotated[Path, typer.Argument(help="Dataset directory")] = Path("data/retail_v1")) -> None:
    """Validate schema, counts, family isolation, oracle consistency and manifest hashes."""
    from agentcheck.dataset import validate_dataset

    report = validate_dataset(path)
    for warning in report.warnings:
        echo(f"warning: {warning}")
    for error in report.errors:
        echo(f"ERROR  {error}")
    if report.ok:
        st = report.stats
        echo(f"ok: {st['cases']} cases, {st['families']} families, splits {st['splits']}, "
             f"{st['worlds']} worlds, content {st['content_sha256'][:16]}")
    raise typer.Exit(0 if report.ok else 1)


@app.command()
@handle_errors
def run(
    config: Annotated[Path, typer.Option(help="Agent config YAML")],
    suite: SuiteOpt = "smoke",
    repeats: Annotated[int, typer.Option(min=1)] = 1,
    output: OutputOpt = None,
    mode: ModeOpt = None,
    mutation: Annotated[list[str] | None, typer.Option(help="Controlled mutation (repeatable)")] = None,
    db: DbOpt = None,
) -> None:
    """Execute one configuration against a suite."""
    from agentcheck.comparisons.metrics import summarize
    from agentcheck.workflows import run_single

    ws = workspace(db)
    out = ws.output_dir(output, f"run-{int(time.time())}")
    record = asyncio.run(run_single(ws, config, suite, repeats, out, mode, mutation))
    s = summarize(record)
    echo(f"run {record.run_id} [{record.mode}] {record.status}: {s['passed']}/{s['planned']} passed, "
         f"{s['infrastructure_errors']} infrastructure errors. Report: {out / 'report.md'}")
    raise typer.Exit(0 if s["complete"] and s["failed"] == 0 else (2 if not s["complete"] else 1))


def _print_comparison(result: Any) -> None:
    c = result.comparison
    echo(f"baseline run  {result.baseline.run_id}")
    echo(f"candidate run {result.candidate.run_id}")
    echo(f"comparison    {result.comparison_id}  [{result.candidate.mode}]")
    echo(f"pass: baseline {c['baseline']['passed']}/{c['baseline']['planned']}, "
         f"candidate {c['candidate']['passed']}/{c['candidate']['planned']} "
         f"(delta {c['pass_rate_delta_pp']} pp); regressions {c['counts']['regressions']}, "
         f"improvements {c['counts']['improvements']}")
    for rule in result.gate["rules"]:
        if rule["triggered"]:
            echo(f"  - {rule['rule']}: {rule['detail']}")
    echo(f"GATE {result.gate['status']} (exit {result.gate['exit_code']})")
    echo(f"report: {result.paths['report']}\njunit:  {result.paths['junit']}")


@app.command()
@handle_errors
def experiment(
    baseline: Annotated[Path, typer.Option(help="Baseline agent config")],
    candidate: Annotated[Path, typer.Option(help="Candidate agent config")],
    suite: SuiteOpt = "smoke",
    repeats: Annotated[int, typer.Option(min=1)] = 1,
    output: OutputOpt = None,
    gate: GateOpt = Path("configs/gates/default.yaml"),
    mode: ModeOpt = None,
    mutation: Annotated[list[str] | None, typer.Option(help="Controlled mutation applied to the candidate")] = None,
    db: DbOpt = None,
) -> None:
    """Run baseline and candidate on identical cases and seeds, then compare and gate."""
    from agentcheck.workflows import experiment as run_experiment

    ws = workspace(db)
    out = ws.output_dir(output, f"experiment-{int(time.time())}")
    result = asyncio.run(run_experiment(ws, baseline, candidate, suite, repeats, out, gate, mode, mutation))
    _print_comparison(result)
    raise typer.Exit(result.gate["exit_code"])


@app.command()
@handle_errors
def compare(
    baseline_run: Annotated[str, typer.Argument(help="Run UUID or path to an exported run.json")],
    candidate_run: Annotated[str, typer.Argument(help="Run UUID or path to an exported run.json")],
    output: OutputOpt = None,
    gate: GateOpt = Path("configs/gates/default.yaml"),
    db: DbOpt = None,
) -> None:
    """Compare two existing runs. Incompatible manifests exit 2."""
    from agentcheck.workflows import strict_compare

    ws = workspace(db)
    result = strict_compare(ws, baseline_run, candidate_run, gate,
                            ws.output_dir(output, f"compare-{int(time.time())}"))
    _print_comparison(result)
    raise typer.Exit(result.gate["exit_code"])


@app.command()
@handle_errors
def resume(run_id: Annotated[str, typer.Argument(help="Run UUID")]) -> None:
    """Resume an interrupted run after validating its manifest. Completed trials are kept."""
    from agentcheck.comparisons.metrics import summarize
    from agentcheck.workflows import resume as resume_run

    record = asyncio.run(resume_run(workspace(True), run_id))
    s = summarize(record)
    echo(f"run {record.run_id}: {record.status}, {s['passed']}/{s['planned']} passed")
    raise typer.Exit(0 if s["complete"] else 2)


@app.command()
@handle_errors
def replay(
    run_ref: Annotated[str, typer.Argument(help="Run UUID or exported run.json")],
    output: OutputOpt = None,
    db: DbOpt = None,
) -> None:
    """Re-score saved traces without contacting a model; reports any check differences."""
    from agentcheck.workflows import replay as replay_run

    ws = workspace(db)
    summary = replay_run(ws, run_ref, output)
    echo(f"[replay] {summary['trials']} trials re-scored, {summary['mismatches']} differ from the original")
    raise typer.Exit(0 if summary["mismatches"] == 0 else 1)


@app.command()
@handle_errors
def export(
    run_ref: Annotated[str, typer.Argument(help="Run UUID or exported run.json")],
    output: Annotated[Path, typer.Option(help="Output directory")],
    db: DbOpt = None,
) -> None:
    """Write report.md, results.json, junit.xml and traces for one run."""
    from agentcheck.workflows import export_run

    paths = export_run(workspace(db), run_ref, output)
    for name, path in paths.items():
        echo(f"{name}: {path}")


@app.command("import")
@handle_errors
def import_(path: Annotated[Path, typer.Argument(help="Exported run directory or run.json")]) -> None:
    """Import an exported run idempotently (same UUID and content: no-op; conflict: error)."""
    from agentcheck.workflows import import_run

    echo(import_run(workspace(True), path))


@app.command("mutation-check")
@handle_errors
def mutation_check(
    all_: Annotated[bool, typer.Option("--all", help="Run every controlled mutation")] = False,
    mutation: Annotated[list[str] | None, typer.Option(help="Mutation name (repeatable)")] = None,
    output: OutputOpt = None,
    db: DbOpt = False,
) -> None:
    """Prove the evaluator detects each controlled mutation. Exits 0 only if all are detected."""
    from agentcheck.samples.retail.mutations import MUTATIONS
    from agentcheck.workflows import mutation_check as check

    names = list(MUTATIONS) if all_ or not mutation else mutation
    ws = workspace(db)
    out = ws.output_dir(output, "mutations")
    summary = asyncio.run(check(ws, names, out))
    echo(f"{summary['detected']}/{summary['total']} controlled mutations detected [fixture mode]. "
         f"Report: {out / 'mutation_report.md'}")
    raise typer.Exit(0 if summary["detected"] == summary["total"] else 1)


@app.command()
@handle_errors
def demo(
    mode: Annotated[str, typer.Option(help="Only 'fixture' is supported")] = "fixture",
    output: OutputOpt = None,
    gate: GateOpt = Path("configs/gates/default.yaml"),
    db: DbOpt = False,
) -> None:
    """Offline demo: baseline, deliberate bug, failing comparison, fix, passing comparison."""
    from agentcheck.errors import ConfigurationError
    from agentcheck.workflows import demo as run_demo

    if mode != "fixture":
        raise ConfigurationError("demo runs in fixture mode; use `experiment` for live models")
    ws = workspace(db)
    out = ws.output_dir(output, "demo")
    summary = asyncio.run(run_demo(ws, out, gate))
    echo("FIXTURE MODE (no language model)")
    echo(f"regression report: {summary['reports']['regression']}")
    echo(f"fix report:        {summary['reports']['fix']}")
    echo("expected detection: " + ("yes" if summary["expected_detection"] else "NO"))
    raise typer.Exit(0 if summary["expected_detection"] else 1)


@app.command()
@handle_errors
def judge(
    run_ref: Annotated[str | None, typer.Option("--run", help="Run UUID or run.json to review")] = None,
    corpus: Annotated[Path | None, typer.Option(help="Judge corpus JSONL (data/judge_v1/*.jsonl)")] = None,
    config: Annotated[Path, typer.Option(help="Judge config")] = Path("configs/judge/local.yaml"),
    output: OutputOpt = None,
    db: DbOpt = None,
) -> None:
    """Advisory LLM review of user-facing messages. Never changes deterministic results."""
    from agentcheck.errors import ConfigurationError
    from agentcheck.evaluators.judge import Judge, judge_input_from_trial, load_judge_config
    from agentcheck.model_clients.ollama import OllamaModelClient
    from agentcheck.runner.execution import latest_trials
    from agentcheck.workflows import load_record

    if (run_ref is None) == (corpus is None):
        raise ConfigurationError("pass exactly one of --run or --corpus")
    ws = workspace(db)
    cfg = load_judge_config(ws.settings.resolve(config))
    rubric = ws.settings.resolve(cfg.rubric_path).read_text("utf-8")
    client = OllamaModelClient(ws.settings.ollama_base_url)
    reviewer = Judge(client, cfg, rubric)
    out = ws.output_dir(output, f"judge-{int(time.time())}")
    out.mkdir(parents=True, exist_ok=True)

    async def go() -> list[dict[str, Any]]:
        await client.model_fingerprint(cfg.model)
        rows: list[dict[str, Any]] = []
        if corpus is not None:
            for line in corpus.read_text("utf-8").splitlines():
                item = json.loads(line)
                result = await reviewer.review(item["judge_input"])
                rows.append({"response_id": item["response_id"], **result})
                echo(f"{item['response_id']}: {result.get('verdict') or 'needs_review'}")
            return rows
        assert run_ref is not None
        record = load_record(ws, run_ref)
        attempt_ids = ws.repo.attempt_ids(record.run_id) if ws.repo is not None else {}
        for trial in latest_trials(record.trials).values():
            judge_input = judge_input_from_trial(trial)
            result = (await reviewer.review(judge_input)) if judge_input else {
                "verdict": None, "semantic_review_status": "needs_review", "error": "no final response"}
            rows.append({"case_id": trial.case_id, "repeat_index": trial.repeat_index,
                         "structured_pass": trial.structured_pass, **result})
            key = (trial.case_id, trial.repeat_index)
            if ws.repo is not None and key in attempt_ids:
                ws.repo.add_judge_review(attempt_ids[key], reviewer.config_sha256, reviewer.rubric_sha256,
                                         result.get("verdict") or "needs_review", result)
            echo(f"{trial.case_id} r{trial.repeat_index}: {result['semantic_review_status']}")
        return rows

    rows = asyncio.run(go())
    (out / "judge_results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    if run_ref is not None:
        from agentcheck.comparisons.reports import write_review_queue

        write_review_queue(out / "review_queue.csv", [
            {**r, "failure_types": ";".join(r.get("failure_types") or []),
             "explanation": r.get("explanation") or r.get("error", "")}
            for r in rows if r["semantic_review_status"] != "advisory_pass"
        ])
    (out / "judge_manifest.json").write_text(json.dumps({
        "judge": cfg.model_dump(), "config_sha256": reviewer.config_sha256,
        "rubric_sha256": reviewer.rubric_sha256, "advisory": True,
        "limitation": "Judge and actor are both Qwen3 models; errors may be correlated."
        if cfg.same_family_as_actor else None}, indent=2), "utf-8")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["semantic_review_status"]] = counts.get(r["semantic_review_status"], 0) + 1
    echo(f"advisory review written to {out / 'judge_results.jsonl'}: {counts}")


@app.command("judge-calibrate")
@handle_errors
def judge_calibrate(
    judge_results: Annotated[Path, typer.Option(help="judge_results.jsonl from `agentcheck judge --corpus`")],
    labels: Annotated[Path, typer.Option(help="Review CSV with human labels")] = Path("data/judge_v1/review.csv"),
    corpus: Annotated[Path, typer.Option(help="Corpus split to score")] = Path("data/judge_v1/evaluation.jsonl"),
    output: OutputOpt = None,
) -> None:
    """Compare judge verdicts with verified human labels. Pending until a person reviews labels."""
    from agentcheck.evaluators.calibration import calibrate

    ids = {json.loads(line)["response_id"] for line in corpus.read_text("utf-8").splitlines()}
    verdicts = {}
    for line in judge_results.read_text("utf-8").splitlines():
        row = json.loads(line)
        if row.get("response_id") in ids:
            verdicts[row["response_id"]] = row.get("verdict")
    report = calibrate(labels, verdicts)
    if report["status"] == "pending":
        echo(report["message"])
    else:
        m = report["metrics"]
        echo(f"reviewed {report['reviewed_rows']} responses by {report['reviewers']}; "
             f"balanced accuracy {m['balanced_accuracy']}, FPR {m['false_positive_rate']['value']}, "
             f"coverage {m['coverage']['value']}")
    out = settings().resolve(output or Path("artifacts/calibration"))
    out.mkdir(parents=True, exist_ok=True)
    (out / ("calibration_draft.json" if report["status"] == "pending" else "calibration.json")).write_text(
        json.dumps(report, indent=2), "utf-8")


@app.command("labels-import")
@handle_errors
def labels_import(
    source: Annotated[Path, typer.Argument(help="CSV with response_id, human_label, reviewer_id, reviewed_at")],
    target: Annotated[Path, typer.Option(help="Review file to update")] = Path("data/judge_v1/review.csv"),
) -> None:
    """Import human-reviewed labels. Rows without reviewer metadata are rejected."""
    from agentcheck.evaluators.calibration import import_labels

    result = import_labels(source, target)
    echo(f"accepted {result['accepted']}, rejected {result['rejected']}")
    raise typer.Exit(0 if result["rejected"] == 0 else 1)


@app.command()
@handle_errors
def serve(
    host: Annotated[str, typer.Option(help="Bind address (local only by design)")] = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Serve the read-only dashboard on localhost."""
    import uvicorn

    from agentcheck.errors import ConfigurationError

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConfigurationError("version 1 binds to localhost only (no authentication)")
    uvicorn.run("agentcheck.web.app:create_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    app()
