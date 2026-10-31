"""Advisory local judge for the user-facing ``message``.

The judge never changes a deterministic result. A malformed or missing judge
response becomes ``needs_review``, never a pass. Each response is judged
independently and without knowing which configuration produced it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentcheck.canonical import sha256_json, sha256_text
from agentcheck.errors import ConfigurationError, InfrastructureError
from agentcheck.model_clients.base import ModelClient, ModelConfig
from agentcheck.schemas.protocol import _inline_refs
from agentcheck.schemas.results import SemanticReviewStatus, TraceEvent, TrialResult

FAILURE_TYPES = (
    "unsupported_claim", "inconsistent_with_fields", "wrong_amount", "wrong_order",
    "unsupported_action_claim", "unsupported_certainty", "contradictory_reason",
    "misleading_recovery", "irrelevant",
)


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "fail", "uncertain"]
    failure_types: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)
    explanation: str = Field(max_length=600)


class JudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    judge_id: str
    provider: Literal["ollama", "scripted"] = "ollama"
    model: str = "qwen3:8b-q4_K_M"
    rubric_path: str = "prompts/judge_v1.txt"
    think: bool = False
    temperature: float = 0.0
    num_ctx: int = 8192
    num_predict: int = 400
    request_timeout_seconds: float = 180
    same_family_as_actor: bool = True


def load_judge_config(path: Path) -> JudgeConfig:
    try:
        return JudgeConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"invalid judge config {path}: {exc}") from exc


def judge_schema() -> dict[str, Any]:
    schema = JudgeVerdict.model_json_schema()
    inlined: dict[str, Any] = _inline_refs(schema, schema.get("$defs", {}))
    return inlined


def status_for(verdict: str | None) -> SemanticReviewStatus:
    return {"pass": "advisory_pass", "fail": "advisory_fail"}.get(verdict or "", "needs_review")  # type: ignore[return-value]


def judge_input_from_trial(trial: TrialResult) -> dict[str, Any] | None:
    """Visible conversation plus the last structured answer. No case labels."""
    finals = [e for e in trial.events if e.event_type == "assistant_final"]
    if not finals:
        return None
    return build_judge_input(
        user_messages=[e.payload["text"] for e in trial.events if e.event_type == "user_message"],
        observations=observations(trial.events),
        response=finals[-1].payload["response"],
    )


def observations(events: list[TraceEvent]) -> list[dict[str, Any]]:
    return [
        {"event_id": e.event_id, "tool_name": e.payload.get("tool_name"),
         "arguments": e.payload.get("arguments"), "result": e.payload.get("result")}
        for e in events if e.event_type in ("tool_result", "tool_error", "tool_rejected")
    ]


def build_judge_input(user_messages: list[str], observations: list[dict[str, Any]],
                      response: dict[str, Any]) -> dict[str, Any]:
    structured = {k: v for k, v in response.items() if k != "message"}
    return {
        "user_messages": user_messages,
        "tool_observations": observations,
        "structured_answer": structured,
        "message": response.get("message", ""),
    }


class Judge:
    def __init__(self, client: ModelClient, config: JudgeConfig, rubric: str) -> None:
        self.client = client
        self.config = config
        self.rubric = rubric
        self.rubric_sha256 = sha256_text(rubric)
        self.config_sha256 = sha256_json(config.model_dump())

    async def review(self, judge_input: dict[str, Any]) -> dict[str, Any]:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": self.rubric},
            {"role": "user", "content": json.dumps(judge_input, ensure_ascii=False)},
        ]
        try:
            response = await self.client.next_action(
                messages, judge_schema(),
                ModelConfig(model=self.config.model, think=self.config.think,
                            temperature=self.config.temperature, num_ctx=self.config.num_ctx,
                            num_predict=self.config.num_predict, seed=42,
                            request_timeout_seconds=self.config.request_timeout_seconds),
            )
        except InfrastructureError as exc:
            return {"verdict": None, "semantic_review_status": "needs_review", "error": str(exc)}
        try:
            verdict = JudgeVerdict.model_validate_json(response.content)
        except ValidationError as exc:
            return {"verdict": None, "semantic_review_status": "needs_review",
                    "error": f"malformed judge output: {exc.errors()[0]['msg']}",
                    "raw": response.content[:2000]}
        return {**verdict.model_dump(), "semantic_review_status": status_for(verdict.verdict),
                "latency_ms": response.latency_ms}
