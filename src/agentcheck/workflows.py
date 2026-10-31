"""Command workflows. The CLI is a thin wrapper around these functions."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentcheck.canonical import canonical_json, dumps_pretty, sha256_text
from agentcheck.comparisons.gates import evaluate_gate, load_gate
from agentcheck.comparisons.metrics import RunRecord, compare, first_divergence
from agentcheck.comparisons.reports import (
    comparison_markdown,
    junit_xml,
    md,
    read_run,
    run_export,
    write_comparison,
    write_run,
    write_single_run,
)
from agentcheck.dataset import Dataset, load_dataset
from agentcheck.errors import ConfigurationError, DatasetError, IncompatibleRunsError
from agentcheck.model_clients.ollama import OllamaModelClient
from agentcheck.runner.execution import (
    Runner,
    RunState,
    execution_from_events,
    latest_trials,
    load_prompt,
    make_client,
    mode_for,
    mutation_source,
    new_run_id,
    score,
    seeds_for,
)
from agentcheck.runner.manifests import build_manifest, compatibility_problems
from agentcheck.samples.retail.mutations import MUTATIONS, validate_mutations
from agentcheck.schemas.config import AgentConfig, load_agent_config
from agentcheck.settings import Settings
from agentcheck.storage.repository import DbRecorder, Repository, make_engine

Echo = Callable[[str], None]
FIXTURE_MODEL = "reference-policy"


@dataclass
class Workspace:
    settings: Settings
    repo: Repository | None
    echo: Echo

    @property
    def root(self) -> Path:
        return self.settings.project_root

    def dataset(self) -> Dataset:
        return load_dataset(self.settings.resolve(self.settings.dataset_dir))

    def output_dir(self, path: Path | None, default: str) -> Path:
        if path is None:
            return self.settings.resolve(self.settings.artifacts_dir) / default
        return path if path.is_absolute() else self.root / path


def open_workspace(settings: Settings, use_db: bool | None, echo: Echo) -> Workspace:
    """``use_db=None`` means: use the database when DATABASE_URL is configured."""
    repo = None
    if use_db is True and not settings.database_url:
        raise ConfigurationError("--db requested but DATABASE_URL is not set")
    if (use_db is None and settings.database_url) or use_db:
        assert settings.database_url
        repo = Repository(make_engine(settings.database_url))
    return Workspace(settings, repo, echo)


def configure(path: Path, mode: str | None, mutations: list[str] | None, root: Path) -> AgentConfig:
    config = load_agent_config(path if path.is_absolute() else root / path)
    update: dict[str, Any] = {}
    if mode == "fixture":
        update.update(provider="scripted", model=FIXTURE_MODEL)
    elif mode == "live_model" and config.provider != "ollama":
        raise ConfigurationError(f"{path}: live_model mode requires provider: ollama")
    if mutations:
        validate_mutations(mutations)
        update["mutations"] = sorted(set(config.mutations) | set(mutations))
    return config.model_copy(update=update) if update else config


async def model_environment(config: AgentConfig, settings: Settings) -> tuple[dict[str, Any] | None, str | None]:
    if config.provider != "ollama":
        return None, None
    client = OllamaModelClient(settings.ollama_base_url)
    version = await client.version()
    fingerprint = await client.model_fingerprint(config.model)
    return fingerprint, version


async def prepare_run(
    ws: Workspace, config: AgentConfig, config_path: str, side: str, dataset: Dataset, suite: str,
    repeats: int, case_ids: list[str] | None = None,
) -> RunState:
    cases = dataset.select(case_ids) if case_ids else dataset.suite(suite)
    seeds = seeds_for(repeats)
    fingerprint, version = await model_environment(config, ws.settings)
    run_id = new_run_id()
    manifest = build_manifest(
        run_id=run_id, config=config, config_path=config_path,
        prompt_text=load_prompt(config, ws.root), dataset_manifest=dataset.manifest,
        suite=suite if not case_ids else f"custom:{len(case_ids)}", cases=cases, seeds=seeds,
        mode=mode_for(config), mutation_source=mutation_source(config), project_root=ws.root,
        model_fingerprint=fingerprint, ollama_version=version,
    )
    state = RunState(run_id=run_id, side=side, config=config, config_path=config_path,  # type: ignore[arg-type]
                     manifest=manifest, cases=cases, seeds=seeds)
    if ws.repo is not None:
        ws.repo.create_run(state)
    return state


def runner(ws: Workspace, progress: bool = True) -> Runner:
    return Runner(ws.root, ws.settings.ollama_base_url,
                  DbRecorder(ws.repo) if ws.repo is not None else None,
                  ws.echo if progress else None)


def _finish(ws: Workspace, state: RunState) -> None:
    if ws.repo is not None:
        ws.repo.set_run_status(state.run_id, state.status)
        ws.repo.set_content_hash(state.run_id, run_export(RunRecord.from_state(state))["content_sha256"])


async def run_single(ws: Workspace, config_path: Path, suite: str, repeats: int, output: Path,
                     mode: str | None = None, mutations: list[str] | None = None) -> RunRecord:
    config = configure(config_path, mode, mutations, ws.root)
    state = await prepare_run(ws, config, str(config_path), "single", ws.dataset(), suite, repeats)
    try:
        await runner(ws).run(state)
    finally:
        _finish(ws, state)
    record = RunRecord.from_state(state)
    write_single_run(output, record)
    return record


@dataclass
class ComparisonResult:
    comparison_id: str
    baseline: RunRecord
    candidate: RunRecord
    comparison: dict[str, Any]
    gate: dict[str, Any]
    compat: list[str]
    paths: dict[str, Path]


def compare_records(ws: Workspace, baseline: RunRecord, candidate: RunRecord, gate_path: Path,
                    output: Path) -> ComparisonResult:
    gate_cfg, _ = load_gate(gate_path if gate_path.is_absolute() else ws.root / gate_path)
    baseline.side, candidate.side = "baseline", "candidate"
    compat = compatibility_problems(baseline.manifest, candidate.manifest)
    comparison = compare(baseline, candidate)
    gate = evaluate_gate(comparison, gate_cfg, compat)
    comparison_id = str(uuid.uuid4())
    paths = write_comparison(output, baseline, candidate, comparison, gate, compat, comparison_id)
    if ws.repo is not None:
        markdown = comparison_markdown(baseline, candidate, comparison, gate, compat)
        ws.repo.save_comparison(comparison_id, baseline.run_id, candidate.run_id, gate,
                                {"comparison": comparison, "gate": gate, "compatibility_problems": compat},
                                markdown)
    return ComparisonResult(comparison_id, baseline, candidate, comparison, gate, compat, paths)


async def experiment(
    ws: Workspace, baseline_path: Path, candidate_path: Path, suite: str, repeats: int, output: Path,
    gate_path: Path, mode: str | None = None, candidate_mutations: list[str] | None = None,
    case_ids: list[str] | None = None, progress: bool = True,
) -> ComparisonResult:
    dataset = ws.dataset()
    b_cfg = configure(baseline_path, mode, None, ws.root)
    c_cfg = configure(candidate_path, mode, candidate_mutations, ws.root)
    baseline = await prepare_run(ws, b_cfg, str(baseline_path), "baseline", dataset, suite, repeats, case_ids)
    candidate = await prepare_run(ws, c_cfg, str(candidate_path), "candidate", dataset, suite, repeats, case_ids)
    try:
        await runner(ws, progress).experiment(baseline, candidate)
    finally:
        _finish(ws, baseline)
        _finish(ws, candidate)
    return compare_records(ws, RunRecord.from_state(baseline), RunRecord.from_state(candidate), gate_path, output)


def load_record(ws: Workspace, ref: str) -> RunRecord:
    """A run reference is a run UUID (database) or a path to an exported run.json."""
    path = Path(ref)
    if path.suffix == ".json" or path.is_dir():
        file = path / "run.json" if path.is_dir() else path
        if not file.exists():
            raise ConfigurationError(f"run export not found: {file}")
        try:
            return read_run(file)[0]
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
    if ws.repo is None:
        raise ConfigurationError("run IDs need the database; pass a run.json path or set DATABASE_URL")
    try:
        uuid.UUID(ref)
        return ws.repo.load_record(ref)
    except (ValueError, KeyError) as exc:
        raise ConfigurationError(f"unknown run {ref!r}") from exc


def strict_compare(ws: Workspace, baseline_ref: str, candidate_ref: str, gate_path: Path,
                   output: Path) -> ComparisonResult:
    result = compare_records(ws, load_record(ws, baseline_ref), load_record(ws, candidate_ref), gate_path, output)
    if result.compat:
        raise IncompatibleRunsError("incompatible runs:\n  " + "\n  ".join(result.compat)
                                    + f"\nreport written to {result.paths['report']}")
    return result


def replay(ws: Workspace, ref: str, output: Path | None = None) -> dict[str, Any]:
    """Re-score saved traces with the current scorer, without contacting a model."""
    record = load_record(ws, ref)
    dataset = ws.dataset()
    if dataset.content_sha256 != record.manifest["test"]["dataset_sha256"]:
        raise DatasetError("dataset content differs from the run's manifest; replay would be invalid")
    index = dataset.by_id()
    rows, mismatches = [], 0
    rescored = []
    for trial in record.trials:
        if trial.status in ("infrastructure_error", "cancelled"):
            continue
        loaded = dataset.load_case(index[trial.case_id])
        execution = execution_from_events(trial.events)
        new = score(loaded, execution, repeat_index=trial.repeat_index, seed=trial.seed, mode="replay",
                    mutation=trial.mutation_source, attempt_index=trial.attempt_index)
        before = [(c.code, c.status) for c in trial.checks]
        after = [(c.code, c.status) for c in new.checks]
        same = before == after
        mismatches += not same
        rows.append({"case_id": trial.case_id, "repeat_index": trial.repeat_index, "original": trial.status,
                     "replayed": new.status, "identical_checks": same})
        new.events = trial.events
        rescored.append(new)
    summary = {"run_id": record.run_id, "evaluation_mode": "replay", "trials": len(rows),
               "mismatches": mismatches, "rows": rows}
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        (output / "replay.json").write_text(dumps_pretty(summary), "utf-8")
        replay_record = RunRecord(record.run_id, record.side, {**record.manifest, "test": {
            **record.manifest["test"], "evaluation_mode_original": record.mode}}, rescored, record.status)
        (output / "junit.xml").write_text(junit_xml([("replay", replay_record.trials)]), "utf-8")
    return summary


async def resume(ws: Workspace, run_id: str) -> RunRecord:
    if ws.repo is None:
        raise ConfigurationError("resume needs the database (DATABASE_URL)")
    row = ws.repo.get_run(run_id)
    if row is None:
        raise ConfigurationError(f"unknown run {run_id}")
    if row.status == "completed":
        ws.echo("run already completed; nothing to resume")
        return ws.repo.load_record(run_id)
    manifest = row.manifest
    dataset = ws.dataset()
    if dataset.content_sha256 != manifest["test"]["dataset_sha256"]:
        raise IncompatibleRunsError("dataset changed since the run started; cannot resume")
    config = AgentConfig.model_validate(manifest["agent"]["config"])
    if sha256_text(load_prompt(config, ws.root)) != manifest["agent"]["prompt_sha256"]:
        raise IncompatibleRunsError("prompt file changed since the run started; cannot resume")
    fingerprint, _ = await model_environment(config, ws.settings)
    expected_digest = (manifest["agent"].get("model") or {}).get("digest")
    if fingerprint is not None and fingerprint.get("digest") != expected_digest:
        raise IncompatibleRunsError(
            f"installed model digest {fingerprint.get('digest')} != recorded {expected_digest}")

    abandoned = ws.repo.abandon_interrupted(run_id)
    attempts = ws.repo.attempt_index_map(run_id)
    done = {k for k, (_, status) in attempts.items() if status in ("passed", "failed")}
    state = RunState(run_id=run_id, side=row.side, config=config,  # type: ignore[arg-type]
                     config_path=manifest["agent"]["config_path"], manifest=manifest,
                     cases=dataset.select(manifest["test"]["case_ids"]), seeds=list(manifest["test"]["seeds"]))
    run_runner = runner(ws)
    assert isinstance(run_runner.recorder, DbRecorder)
    pending = [(c.case.case_id, r) for c in state.cases for r in range(1, len(state.seeds) + 1)
               if (c.case.case_id, r) not in done]
    ws.echo(f"resuming {run_id}: {len(done)} trials kept, {abandoned} interrupted attempt(s) abandoned, "
            f"{len(pending)} to run")
    for key in pending:
        if key in attempts:
            run_runner.recorder.rerun_reasons[key] = f"resume after {attempts[key][1]}"
    ws.repo.set_run_status(run_id, "running")
    try:
        prompt = load_prompt(config, ws.root)
        client = make_client(config, ws.settings.ollama_base_url)
        index = {c.case.case_id: c for c in state.cases}
        for case_id, repeat in pending:
            next_attempt = attempts.get((case_id, repeat), (0, ""))[0] + 1
            await run_runner.trial(state, client, prompt, index[case_id], repeat,
                                   state.seeds[repeat - 1], next_attempt)
    finally:
        record = ws.repo.load_record(run_id)
        latest = latest_trials(record.trials)
        complete = len(latest) == len(state.cases) * len(state.seeds) and all(
            t.status in ("passed", "failed") for t in latest.values())
        ws.repo.set_run_status(run_id, "completed" if complete else "incomplete")
    return ws.repo.load_record(run_id)


def import_run(ws: Workspace, path: Path) -> str:
    if ws.repo is None:
        raise ConfigurationError("import needs the database (DATABASE_URL)")
    file = path / "run.json" if path.is_dir() else path
    try:
        record, digest = read_run(file)
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"cannot import {file}: {exc}") from exc
    try:
        return ws.repo.import_record(record, digest)
    except ValueError as exc:
        raise IncompatibleRunsError(str(exc)) from exc


def export_run(ws: Workspace, ref: str, output: Path) -> dict[str, Path]:
    return write_single_run(output, load_record(ws, ref))


async def mutation_check(ws: Workspace, names: list[str], output: Path) -> dict[str, Any]:
    """Run every selected mutation against a passing scripted control.

    Detection requires: the control passes, the mutation changes the trace, and at
    least one of the mutation's targeted checks fails. Unrelated checks are not
    required to fail.
    """
    validate_mutations(names)
    dataset = ws.dataset()
    base_path = Path("configs/agents/baseline.yaml")
    results: dict[str, Any] = {}
    for name in names:
        spec = MUTATIONS[name]
        control_cfg = configure(base_path, "fixture", None, ws.root)
        mutated_cfg = configure(base_path, "fixture", [name], ws.root)
        control = await prepare_run(ws, control_cfg, str(base_path), "baseline", dataset, "mutation",
                                    1, list(spec.target_cases))
        mutated = await prepare_run(ws, mutated_cfg, str(base_path), "candidate", dataset, "mutation",
                                    1, list(spec.target_cases))
        r = runner(ws, progress=False)
        await r.run(control)
        await r.run(mutated)
        _finish(ws, control)
        _finish(ws, mutated)
        control_ok = all(t.status == "passed" for t in control.trials)
        evidence = []
        for ct, mt in zip(control.trials, mutated.trials, strict=True):
            targeted = [c for c in mt.failed_checks if c.code in spec.expected_checks]
            evidence.append({
                "case_id": mt.case_id, "control_status": ct.status, "mutated_status": mt.status,
                "behaviour_changed": first_divergence(ct, mt) is not None,
                "first_divergence": first_divergence(ct, mt),
                "targeted_failures": [{"code": c.code, "expected": c.expected, "actual": c.actual,
                                       "message": c.message, "event_ids": c.event_ids} for c in targeted],
                "other_failures": [c.code for c in mt.failed_checks if c.code not in spec.expected_checks],
            })
        detected = control_ok and any(e["behaviour_changed"] and e["targeted_failures"] for e in evidence)
        results[name] = {"layer": spec.layer, "description": spec.description,
                         "expected_checks": list(spec.expected_checks), "control_passed": control_ok,
                         "detected": detected, "evidence": evidence,
                         "control_run_id": control.run_id, "mutated_run_id": mutated.run_id}
        write_run(RunRecord.from_state(control), output / "runs" / name / "control")
        write_run(RunRecord.from_state(mutated), output / "runs" / name / "mutated")
        ws.echo(f"{'DETECTED' if detected else 'MISSED  '} {name:22s} ({spec.layer}) "
                f"control={'pass' if control_ok else 'FAIL'}")
    summary = {"evaluation_mode": "fixture", "mutation_source": "controlled",
               "detected": sum(r["detected"] for r in results.values()), "total": len(results),
               "dataset_sha256": dataset.content_sha256, "mutations": results}
    output.mkdir(parents=True, exist_ok=True)
    (output / "mutations.json").write_text(dumps_pretty(summary), "utf-8")
    (output / "mutation_report.md").write_text(mutation_markdown(summary), "utf-8")
    (output / "junit.xml").write_text(mutation_junit(summary), "utf-8")
    return summary


def mutation_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Controlled mutation suite", "",
        "> **FIXTURE MODE**: scripted reference policy; mutations are deliberate fault injections, "
        "not observed model behaviour.", "",
        f"**{summary['detected']}/{summary['total']} controlled mutations detected.** This measures "
        "this defined set of injected bugs, not coverage of all possible agent failures.", "",
        "| Mutation | Layer | Control | Detected | Targeted checks that failed |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, r in summary["mutations"].items():
        failed = sorted({f["code"] for e in r["evidence"] for f in e["targeted_failures"]})
        lines.append(f"| `{name}` | {r['layer']} | {'pass' if r['control_passed'] else 'FAIL'} | "
                     f"{'yes' if r['detected'] else '**no**'} | {', '.join(failed) or '-'} |")
    lines += ["", "## Evidence", ""]
    for name, r in summary["mutations"].items():
        lines += [f"### `{name}`", "", md(r["description"]), ""]
        for e in r["evidence"]:
            lines.append(f"- `{e['case_id']}`: control {e['control_status']}, mutated {e['mutated_status']}")
            for f in e["targeted_failures"][:3]:
                lines.append(f"  - **{f['code']}**: {md(f['message'], 240)} "
                             f"(expected {md(f['expected'], 120)}; actual {md(f['actual'], 120)})")
        lines.append("")
    return "\n".join(lines) + "\n"


def mutation_junit(summary: dict[str, Any]) -> str:
    import xml.etree.ElementTree as ET

    root = ET.Element("testsuites", name="agentcheck-mutations")
    suite = ET.SubElement(root, "testsuite", name="controlled_mutations", tests=str(summary["total"]),
                          failures=str(summary["total"] - summary["detected"]))
    for name, r in summary["mutations"].items():
        case = ET.SubElement(suite, "testcase", classname=f"mutations.{r['layer']}", name=name)
        if not r["detected"]:
            node = ET.SubElement(case, "failure", message="mutation not detected", type="undetected")
            node.text = canonical_json(r["evidence"])[:4000]
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


DEMO_MUTATION = "cutoff_exclusive"


async def demo(ws: Workspace, output: Path, gate_path: Path) -> dict[str, Any]:
    """Baseline -> deliberate bug -> failing comparison -> fix -> passing comparison."""
    base = Path("configs/agents/baseline.yaml")
    ws.echo(f"[1/2] baseline vs candidate with controlled mutation `{DEMO_MUTATION}` (dev split, fixture mode)")
    broken = await experiment(ws, base, base, "dev", 1, output / "01_regression", gate_path,
                              mode="fixture", candidate_mutations=[DEMO_MUTATION], progress=False)
    ws.echo(f"      gate {broken.gate['status']}; regressions: "
            f"{[c['case_id'] for c in broken.comparison['regressed_cases']]}")
    ws.echo("[2/2] baseline vs fixed candidate (mutation removed)")
    fixed = await experiment(ws, base, base, "dev", 1, output / "02_fix", gate_path, mode="fixture",
                             progress=False)
    ws.echo(f"      gate {fixed.gate['status']}")
    expected_hit = any(c["case_id"].startswith("time_boundary_dev_001") for c in broken.comparison["regressed_cases"])
    ok = broken.gate["status"] == "FAIL" and expected_hit and fixed.gate["status"] == "PASS"
    summary = {"evaluation_mode": "fixture", "mutation": DEMO_MUTATION, "expected_detection": ok,
               "regression_gate": broken.gate["status"], "fix_gate": fixed.gate["status"],
               "regressed_cases": [c["case_id"] for c in broken.comparison["regressed_cases"]],
               "reports": {"regression": str(broken.paths["report"]), "fix": str(fixed.paths["report"])},
               "comparison_ids": [broken.comparison_id, fixed.comparison_id]}
    (output / "demo.json").write_text(dumps_pretty(summary), "utf-8")
    return summary
