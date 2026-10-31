"""Trial execution and matched experiment orchestration.

A *trial* is one case, one repeat, one configuration. Each trial gets a fresh
deep copy of its world, a fresh fault schedule and a fresh scripted user. In an
experiment, baseline and candidate run the same ordered cases and repeat seeds,
serially, alternating which side goes first on a reproducible schedule.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from agentcheck.agent.structured_agent import StructuredActionAgent
from agentcheck.canonical import sha256_json
from agentcheck.errors import AgentLimitExceeded, ConfigurationError, InfrastructureError
from agentcheck.evaluators.deterministic import DeterministicEvaluator
from agentcheck.model_clients.base import ModelClient, ModelConfig
from agentcheck.model_clients.ollama import OllamaModelClient
from agentcheck.model_clients.scripted import ReferencePolicyClient
from agentcheck.runner.dialogue import ScriptedUser
from agentcheck.runner.faults import FaultSchedule
from agentcheck.runner.trace import TraceSink
from agentcheck.samples.retail.mutations import MUTATIONS, action_mutators, tool_class
from agentcheck.schemas.cases import AgentCaseInput, LoadedCase
from agentcheck.schemas.config import AgentConfig
from agentcheck.schemas.results import (
    AgentExecution,
    EvaluationMode,
    ExecutionStatus,
    TraceEvent,
    TrialMetrics,
    TrialResult,
)
from agentcheck.tools.dispatcher import REGISTERED_TOOLS, ToolDispatcher

DEFAULT_SEEDS = (42, 43, 44)
SESSION_CONTEXT = {"authenticated": True, "channel": "chat", "store": "Northstar Demo Store"}


def seeds_for(repeats: int) -> list[int]:
    if repeats < 1:
        raise ConfigurationError("repeats must be at least 1")
    return [DEFAULT_SEEDS[i] if i < len(DEFAULT_SEEDS) else 42 + i for i in range(repeats)]


def mode_for(config: AgentConfig) -> EvaluationMode:
    return "fixture" if config.provider == "scripted" else "live_model"


def mutation_source(config: AgentConfig) -> str | None:
    if not config.mutations:
        return None
    return ",".join(f"{MUTATIONS[m].layer}:{m}" for m in config.mutations)


def make_client(config: AgentConfig, ollama_base_url: str) -> ModelClient:
    if config.provider == "scripted":
        return ReferencePolicyClient()
    return OllamaModelClient(ollama_base_url)


def load_prompt(config: AgentConfig, project_root: Path) -> str:
    path = Path(config.prompt_path)
    path = path if path.is_absolute() else project_root / path
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigurationError(f"prompt file not found: {path}") from exc


def trial_metrics(events: list[TraceEvent], forbidden: set[str]) -> TrialMetrics:
    m = TrialMetrics()
    prompt_tokens = completion_tokens = 0
    inference = 0.0
    have_tokens = have_inference = False
    for e in events:
        p = e.payload
        if e.event_type == "model_request":
            m.model_calls += 1
        elif e.event_type == "model_response":
            m.model_responses += 1
            if not p.get("repair"):
                m.first_attempt_total += 1
            if isinstance(p.get("prompt_tokens"), int):
                prompt_tokens += p["prompt_tokens"]
                have_tokens = True
            if isinstance(p.get("completion_tokens"), int):
                completion_tokens += p["completion_tokens"]
            if isinstance(p.get("inference_ms"), int | float):
                inference += float(p["inference_ms"])
                have_inference = True
        elif e.event_type == "protocol_error":
            m.protocol_errors += 1
        elif e.event_type == "repair_requested":
            m.repairs += 1
        elif e.event_type == "tool_attempt":
            m.tool_attempts += 1
            name = p.get("tool_name")
            if name not in REGISTERED_TOOLS or name in forbidden:
                m.forbidden_tool_attempts += 1
        elif e.event_type in ("tool_result", "tool_error"):
            m.tool_executions += 1
            if p.get("tool_name") not in REGISTERED_TOOLS or p.get("tool_name") in forbidden:
                m.forbidden_tool_executions += 1
        elif e.event_type == "case_finished":
            m.latency_ms = e.elapsed_ms
    first_attempt_errors = sum(
        1 for e in events if e.event_type == "protocol_error" and not e.payload.get("repair_attempt")
    )
    m.first_attempt_valid = max(m.first_attempt_total - first_attempt_errors, 0)
    if have_tokens:
        m.prompt_tokens, m.completion_tokens = prompt_tokens, completion_tokens
    if have_inference:
        m.inference_ms = round(inference, 3)
    return m


def execution_from_events(events: list[TraceEvent]) -> AgentExecution:
    """Rebuild an :class:`AgentExecution` from a stored trace (used by replay)."""
    finished = next((e for e in events if e.event_type == "case_finished"), None)
    if finished is None:
        raise InfrastructureError("trace has no case_finished event; attempt was interrupted")
    body = [e for e in events if e.sequence <= finished.sequence]
    p = finished.payload
    return AgentExecution(
        status=p["status"], termination_reason=p.get("termination_reason"), error=p.get("error"),
        events=body, turns_started=p.get("turns_started", 0),
        dialogue_completed=p.get("dialogue_completed", False),
        dialogue_failure=p.get("dialogue_failure"),
        world_sha256_before=p["world_sha256_before"], world_sha256_after=p["world_sha256_after"],
    )


def score(
    loaded: LoadedCase, execution: AgentExecution, *, repeat_index: int, seed: int,
    mode: EvaluationMode, mutation: str | None, attempt_index: int = 1,
    trace: TraceSink | None = None,
) -> TrialResult:
    case = loaded.case
    forbidden = {t for exp in case.expected_turns for b in
                 (exp.any_of if hasattr(exp, "any_of") else [exp]) for t in b.forbidden_tools}
    if execution.status in ("infrastructure_error", "cancelled"):
        checks = []
        status: Any = execution.status
    else:
        checks = DeterministicEvaluator().evaluate(loaded, execution)
        status = "failed" if any(c.status == "fail" for c in checks) else "passed"
        if trace is not None:
            for c in checks:
                trace.emit("check_result", c.model_dump(mode="json"))
    events = trace.events if trace is not None else execution.events
    return TrialResult(
        case_id=case.case_id, category=case.category, split=case.split,
        scenario_family_id=case.scenario_family_id, repeat_index=repeat_index, seed=seed,
        attempt_index=attempt_index, status=status, structured_pass=status == "passed",
        evaluation_mode=mode, mutation_source=mutation,
        termination_reason=execution.termination_reason, error=execution.error,
        checks=checks, metrics=trial_metrics(events, forbidden), events=events,
        execution={
            "status": execution.status, "turns_started": execution.turns_started,
            "dialogue_completed": execution.dialogue_completed,
            "dialogue_failure": execution.dialogue_failure,
        },
    )


async def execute_trial(
    loaded: LoadedCase,
    config: AgentConfig,
    client: ModelClient,
    system_prompt: str,
    *,
    repeat_index: int,
    seed: int,
    attempt_index: int = 1,
    on_event: Callable[[TraceEvent], None] | None = None,
) -> TrialResult:
    case = loaded.case
    mode = mode_for(config)
    mutation = mutation_source(config)
    trace = TraceSink(on_event)
    trace.emit("case_started", {
        "case_id": case.case_id, "repeat_index": repeat_index, "seed": seed,
        "attempt_index": attempt_index, "agent_id": config.agent_id, "evaluation_mode": mode,
        "mutation_source": mutation, "clock_utc": case.clock_utc,
    })

    world = loaded.world.model_copy(deep=True)
    before = sha256_json(world)
    tools = tool_class(config.tool_implementation, config.mutations)(
        world, case.session.customer_id, datetime.fromisoformat(case.clock_utc)
    )
    dispatcher = ToolDispatcher(tools, FaultSchedule(list(case.fault_plan)), trace)
    user = ScriptedUser(list(case.dialogue))
    agent = StructuredActionAgent(
        client, system_prompt,
        ModelConfig(
            model=config.model, think=config.think, temperature=config.temperature,
            num_ctx=config.num_ctx, num_predict=config.num_predict, seed=seed,
            request_timeout_seconds=config.request_timeout_seconds,
        ),
        action_mutators(config.mutations),
    )

    status: ExecutionStatus = "completed"
    reason: str | None = None
    error: str | None = None
    try:
        await asyncio.wait_for(
            agent.run_case(AgentCaseInput(session_context=SESSION_CONTEXT, user=user),
                           dispatcher, trace, config),
            timeout=config.case_timeout_seconds,
        )
        if not user.completed:
            status, reason = "agent_failure", "dialogue_predicate_failed"
    except AgentLimitExceeded as exc:
        status, reason, error = "agent_failure", exc.reason, str(exc)
    except TimeoutError:
        status, reason = "agent_failure", "case_timeout"
        error = f"case exceeded {config.case_timeout_seconds}s"
    except InfrastructureError as exc:
        status, reason, error = "infrastructure_error", "infrastructure", str(exc)
    except asyncio.CancelledError:
        status, reason = "cancelled", "operator_interrupt"

    after = sha256_json(world)
    turns = sum(1 for e in trace.events if e.event_type == "user_message")
    trace.emit("case_finished", {
        "status": status, "termination_reason": reason, "error": error, "turns_started": turns,
        "dialogue_completed": user.completed, "dialogue_failure": user.failure,
        "world_sha256_before": before, "world_sha256_after": after,
    })
    execution = AgentExecution(
        status=status, termination_reason=reason, error=error, events=list(trace.events),
        turns_started=turns, dialogue_completed=user.completed, dialogue_failure=user.failure,
        world_sha256_before=before, world_sha256_after=after,
    )
    return score(loaded, execution, repeat_index=repeat_index, seed=seed, mode=mode,
                 mutation=mutation, attempt_index=attempt_index, trace=trace)


class TrialRecorder(Protocol):
    """Persistence hook. Events are recorded as they happen, results at the end."""

    def begin(self, run: RunState, loaded: LoadedCase, repeat_index: int, seed: int,
              attempt_index: int) -> Callable[[TraceEvent], None] | None: ...

    def finish(self, run: RunState, trial: TrialResult) -> None: ...


@dataclass
class RunState:
    run_id: str
    side: Literal["baseline", "candidate", "single"]
    config: AgentConfig
    config_path: str
    manifest: dict[str, Any]
    cases: list[LoadedCase]
    seeds: list[int]
    trials: list[TrialResult] = field(default_factory=list)
    status: str = "running"

    @property
    def mode(self) -> EvaluationMode:
        return mode_for(self.config)

    @property
    def planned(self) -> int:
        return len(self.cases) * len(self.seeds)


def new_run_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Runner:
    project_root: Path
    ollama_base_url: str
    recorder: TrialRecorder | None = None
    progress: Callable[[str], None] | None = None

    async def trial(self, run: RunState, client: ModelClient, prompt: str, loaded: LoadedCase,
                     repeat_index: int, seed: int, attempt_index: int = 1) -> TrialResult:
        on_event = None
        if self.recorder is not None:
            on_event = self.recorder.begin(run, loaded, repeat_index, seed, attempt_index)
        trial = await execute_trial(loaded, run.config, client, prompt, repeat_index=repeat_index,
                                    seed=seed, attempt_index=attempt_index, on_event=on_event)
        run.trials.append(trial)
        if self.recorder is not None:
            self.recorder.finish(run, trial)
        if self.progress is not None:
            failed = ",".join(c.code for c in trial.failed_checks)
            self.progress(
                f"[{run.side:9s}] {trial.case_id} r{repeat_index} {trial.status}"
                + (f" ({failed})" if failed else "")
            )
        return trial

    async def run(self, run: RunState, skip: set[tuple[str, int]] | None = None) -> RunState:
        client = make_client(run.config, self.ollama_base_url)
        prompt = load_prompt(run.config, self.project_root)
        skip = skip or set()
        try:
            for loaded in run.cases:
                for repeat_index, seed in enumerate(run.seeds, start=1):
                    if (loaded.case.case_id, repeat_index) in skip:
                        continue
                    await self.trial(run, client, prompt, loaded, repeat_index, seed)
        except (KeyboardInterrupt, asyncio.CancelledError):
            run.status = "cancelled"
            raise
        run.status = finalize_status(run)
        return run

    async def experiment(self, baseline: RunState, candidate: RunState) -> tuple[RunState, RunState]:
        clients = {
            "baseline": make_client(baseline.config, self.ollama_base_url),
            "candidate": make_client(candidate.config, self.ollama_base_url),
        }
        prompts = {
            "baseline": load_prompt(baseline.config, self.project_root),
            "candidate": load_prompt(candidate.config, self.project_root),
        }
        try:
            for index, loaded in enumerate(baseline.cases):
                for repeat_index, seed in enumerate(baseline.seeds, start=1):
                    order = (baseline, candidate) if (index + repeat_index) % 2 == 1 else (candidate, baseline)
                    for run in order:
                        await self.trial(run, clients[run.side], prompts[run.side], loaded,
                                          repeat_index, seed)
        except (KeyboardInterrupt, asyncio.CancelledError):
            baseline.status = candidate.status = "cancelled"
            raise
        baseline.status = finalize_status(baseline)
        candidate.status = finalize_status(candidate)
        return baseline, candidate


def finalize_status(run: RunState) -> str:
    latest = latest_trials(run.trials)
    if len(latest) < run.planned:
        return "incomplete"
    if any(t.status in ("infrastructure_error", "cancelled") for t in latest.values()):
        return "incomplete"
    return "completed"


def latest_trials(trials: list[TrialResult]) -> dict[tuple[str, int], TrialResult]:
    """Latest scored attempt per (case, repeat). Earlier attempts stay in history."""
    out: dict[tuple[str, int], TrialResult] = {}
    for t in sorted(trials, key=lambda t: t.attempt_index):
        key = (t.case_id, t.repeat_index)
        if key not in out or t.status in ("passed", "failed") or out[key].status not in ("passed", "failed"):
            out[key] = t
    return out
