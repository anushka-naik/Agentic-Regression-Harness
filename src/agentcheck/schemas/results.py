"""Trace, execution and scoring records."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal[
    "case_started",
    "case_finished",
    "user_message",
    "model_request",
    "model_response",
    "protocol_error",
    "repair_requested",
    "tool_attempt",
    "tool_rejected",
    "tool_result",
    "tool_error",
    "assistant_final",
    "check_result",
    "judge_result",
]
ExecutionStatus = Literal["completed", "agent_failure", "infrastructure_error", "cancelled"]
TrialStatus = Literal["passed", "failed", "infrastructure_error", "cancelled", "pending"]
CheckStatus = Literal["pass", "fail", "not_applicable"]
Severity = Literal["critical", "error", "warning"]
EvaluationMode = Literal["fixture", "replay", "live_model"]
SemanticReviewStatus = Literal[
    "not_run", "advisory_pass", "advisory_fail", "needs_review", "human_reviewed"
]


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    sequence: int
    event_type: EventType
    payload: dict[str, Any]
    wall_time: str
    elapsed_ms: float


class AgentExecution(BaseModel):
    """Everything the scorer needs, reconstructible from the trace alone."""

    status: ExecutionStatus
    termination_reason: str | None = None
    error: str | None = None
    events: list[TraceEvent]
    turns_started: int = 0
    dialogue_completed: bool = False
    dialogue_failure: str | None = None
    world_sha256_before: str
    world_sha256_after: str


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    status: CheckStatus
    severity: Severity
    expected: Any = None
    actual: Any = None
    message: str = ""
    event_ids: list[str] = Field(default_factory=list)


class TrialMetrics(BaseModel):
    latency_ms: float = 0.0
    model_calls: int = 0
    tool_attempts: int = 0
    tool_executions: int = 0
    model_responses: int = 0
    first_attempt_valid: int = 0
    first_attempt_total: int = 0
    repairs: int = 0
    protocol_errors: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    inference_ms: float | None = None
    forbidden_tool_attempts: int = 0
    forbidden_tool_executions: int = 0


class TrialResult(BaseModel):
    case_id: str
    category: str
    split: str
    scenario_family_id: str
    repeat_index: int
    seed: int
    attempt_index: int = 1
    status: TrialStatus
    structured_pass: bool
    evaluation_mode: EvaluationMode
    mutation_source: str | None = None
    semantic_review_status: SemanticReviewStatus = "not_run"
    termination_reason: str | None = None
    error: str | None = None
    checks: list[CheckResult]
    metrics: TrialMetrics
    events: list[TraceEvent]
    execution: dict[str, Any] = Field(default_factory=dict)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]
