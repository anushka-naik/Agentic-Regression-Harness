"""Model client interface shared by the Ollama and scripted clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelConfig:
    model: str
    think: bool = False
    temperature: float = 0.0
    num_ctx: int = 8192
    num_predict: int = 768
    seed: int | None = None
    request_timeout_seconds: float = 120.0


@dataclass
class ModelResponse:
    content: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    inference_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ModelClient(Protocol):
    #: "ollama" or "scripted"; recorded in manifests.
    provider: str

    async def next_action(
        self,
        messages: list[dict[str, object]],
        schema: dict[str, object],
        config: ModelConfig,
    ) -> ModelResponse: ...
