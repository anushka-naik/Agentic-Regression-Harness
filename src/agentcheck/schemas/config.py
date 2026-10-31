"""Agent configuration files (``configs/agents/*.yaml``)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentcheck.errors import ConfigurationError


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    agent_id: str
    provider: Literal["ollama", "scripted"]
    model: str
    prompt_path: str
    tool_implementation: str = "retail_v1"
    mutations: list[str] = Field(default_factory=list)
    think: bool = False
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    num_ctx: int = Field(default=8192, ge=512)
    num_predict: int = Field(default=768, ge=16)
    max_model_calls_per_turn: int = Field(default=8, ge=1)
    max_model_calls_per_case: int = Field(default=16, ge=1)
    max_tool_attempts_per_turn: int = Field(default=6, ge=1)
    max_user_turns: int = Field(default=3, ge=1, le=3)
    request_timeout_seconds: float = Field(default=120, gt=0)
    case_timeout_seconds: float = Field(default=600, gt=0)
    concurrency: Literal[1] = 1
    format_repair_attempts_per_turn: int = Field(default=1, ge=0, le=3)


#: Fields that change agent behaviour; listed in comparison reports.
BEHAVIOURAL_FIELDS: tuple[str, ...] = (
    "provider", "model", "prompt_path", "tool_implementation", "mutations", "think",
    "temperature", "num_ctx", "num_predict", "max_model_calls_per_turn",
    "max_model_calls_per_case", "max_tool_attempts_per_turn", "max_user_turns",
    "request_timeout_seconds", "case_timeout_seconds", "format_repair_attempts_per_turn",
)


def load_agent_config(path: Path) -> AgentConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"agent config not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path} must contain a mapping")
    try:
        return AgentConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(f"invalid agent config {path}:\n{exc}") from exc
