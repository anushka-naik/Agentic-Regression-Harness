"""Run manifests: a complete fingerprint of inputs and environment.

Two runs may be compared only if everything that defines the *test* matches
(dataset, cases, worlds, policy, oracle, scorer, repeats, seeds, protocol,
evaluation mode). What defines the *agent* (model, prompt, tools, decoding) may
differ deliberately and is listed. Performance numbers are comparable only if
hardware, runtime and concurrency also match.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

import agentcheck
from agentcheck import POLICY_VERSION, PROTOCOL_VERSION, SCHEMA_VERSION
from agentcheck.agent import structured_agent
from agentcheck.canonical import sha256_file, sha256_json, sha256_text
from agentcheck.evaluators import deterministic, retail_oracle
from agentcheck.samples import retail
from agentcheck.samples.retail import tools as retail_tools
from agentcheck.samples.retail.mutations import adapter_mutations, tool_mutations
from agentcheck.schemas.cases import LoadedCase
from agentcheck.schemas.config import BEHAVIOURAL_FIELDS, AgentConfig
from agentcheck.tools import dispatcher

MANIFEST_SCHEMA_VERSION = 1
POLICY_FILE = Path(retail.__file__).parent / "policy.md"

#: Keys under manifest["test"] that must be identical for a valid comparison.
COMPARABLE_KEYS = (
    "dataset_sha256", "case_ids_sha256", "worlds_sha256", "policy_version", "policy_sha256",
    "oracle_sha256", "scorer_version", "scorer_sha256", "repeats", "seeds", "protocol_version",
    "evaluation_mode", "dispatcher_sha256",
)
PERFORMANCE_KEYS = ("machine", "cpu", "ram_bytes", "accelerator", "ollama_version", "concurrency")


def _module_hash(module: Any) -> str:
    return sha256_file(Path(module.__file__))


def _git(project_root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(project_root), *args], capture_output=True,
                             text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def _cpu() -> str:
    try:
        if platform.system() == "Darwin":
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                                  text=True, timeout=5).stdout.strip() or platform.processor()
        if Path("/proc/cpuinfo").exists():
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.processor() or "unknown"


def _ram() -> int | None:
    try:
        if platform.system() == "Darwin":
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                      text=True, timeout=5).stdout.strip())
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _accelerator() -> str:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "Apple Silicon (Metal via Ollama)"
    return "unknown"


def environment(project_root: Path) -> dict[str, Any]:
    lock = project_root / "uv.lock"
    status = _git(project_root, "status", "--porcelain")
    return {
        "python": sys.version.split()[0],
        "agentcheck_version": agentcheck.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": _cpu(),
        "ram_bytes": _ram(),
        "accelerator": _accelerator(),
        "uv_lock_sha256": sha256_file(lock) if lock.exists() else None,
        "git_commit": _git(project_root, "rev-parse", "HEAD"),
        "git_dirty": bool(status) if status is not None else None,
        "packages": {p: _version(p) for p in ("pydantic", "httpx", "sqlalchemy", "fastapi")},
    }


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def build_manifest(
    *,
    run_id: str,
    config: AgentConfig,
    config_path: str,
    prompt_text: str,
    dataset_manifest: dict[str, Any],
    suite: str,
    cases: list[LoadedCase],
    seeds: list[int],
    mode: str,
    mutation_source: str | None,
    project_root: Path,
    model_fingerprint: dict[str, Any] | None = None,
    ollama_version: str | None = None,
) -> dict[str, Any]:
    case_ids = [c.case.case_id for c in cases]
    worlds = {c.case.world_file: c.world_sha256 for c in cases}
    tool_sources = [retail_tools, tool_mutations, adapter_mutations]
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "test": {
            "dataset_name": dataset_manifest.get("name"),
            "dataset_sha256": dataset_manifest["content_sha256"],
            "suite": suite,
            "case_ids": case_ids,
            "case_ids_sha256": sha256_json(case_ids),
            "worlds_sha256": sha256_json(worlds),
            "policy_version": POLICY_VERSION,
            "policy_sha256": sha256_file(POLICY_FILE),
            "oracle_sha256": _module_hash(retail_oracle),
            "scorer_version": deterministic.SCORER_VERSION,
            "scorer_sha256": _module_hash(deterministic),
            "dispatcher_sha256": _module_hash(dispatcher),
            "repeats": len(seeds),
            "seeds": seeds,
            "protocol_version": PROTOCOL_VERSION,
            "evaluation_mode": mode,
            "fixed_clocks": sorted({c.case.clock_utc for c in cases}),
            "fault_schedules_sha256": sha256_json(
                {c.case.case_id: c.case.model_dump(mode="json")["fault_plan"] for c in cases}
            ),
        },
        "agent": {
            "config_path": config_path,
            "config": config.model_dump(mode="json"),
            "config_sha256": sha256_json(config.model_dump(mode="json")),
            "prompt_sha256": sha256_text(prompt_text),
            "tool_implementation": config.tool_implementation,
            "tool_sha256": sha256_json([_module_hash(m) for m in tool_sources]),
            "agent_loop_sha256": _module_hash(structured_agent),
            "mutation_source": mutation_source,
            "model": model_fingerprint or {"tag": config.model, "digest": None,
                                           "note": "scripted reference policy; no model weights"},
        },
        "environment": {
            **environment(project_root),
            "ollama_version": ollama_version,
            "concurrency": config.concurrency,
        },
    }


def compatibility_problems(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    problems = []
    for key in COMPARABLE_KEYS:
        if a["test"].get(key) != b["test"].get(key):
            problems.append(f"{key} differs: {a['test'].get(key)!r} vs {b['test'].get(key)!r}")
    return problems


def agent_differences(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    diffs = []
    ca, cb = a["agent"]["config"], b["agent"]["config"]
    for key in BEHAVIOURAL_FIELDS:
        if ca.get(key) != cb.get(key):
            diffs.append({"field": key, "baseline": ca.get(key), "candidate": cb.get(key)})
    for key in ("prompt_sha256", "tool_sha256", "agent_loop_sha256"):
        if a["agent"].get(key) != b["agent"].get(key):
            diffs.append({"field": key, "baseline": a["agent"][key][:12], "candidate": b["agent"][key][:12]})
    da = (a["agent"].get("model") or {}).get("digest")
    db = (b["agent"].get("model") or {}).get("digest")
    if da != db:
        diffs.append({"field": "model_digest", "baseline": da, "candidate": db})
    return diffs


def performance_comparable(a: dict[str, Any], b: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = [
        f"{k}: {a['environment'].get(k)!r} vs {b['environment'].get(k)!r}"
        for k in PERFORMANCE_KEYS if a["environment"].get(k) != b["environment"].get(k)
    ]
    return not reasons, reasons
