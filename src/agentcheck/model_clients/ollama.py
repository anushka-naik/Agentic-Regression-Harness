"""Ollama client using the native ``/api/chat`` endpoint.

Every request sends ``stream=false``, an explicit ``think`` flag and the action
JSON schema in ``format``. Connection failures, timeouts and server errors are
infrastructure errors, never agent failures.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from agentcheck.errors import ModelUnavailableError
from agentcheck.model_clients.base import ModelConfig, ModelResponse


class OllamaModelClient:
    provider = "ollama"

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(timeout), transport=self._transport
        )

    async def _request(
        self, method: str, path: str, timeout: float, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            async with self._client(timeout) as client:
                response = await client.request(method, path, json=json)
        except httpx.TimeoutException as exc:
            raise ModelUnavailableError(f"Ollama request to {path} timed out after {timeout}s") from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(
                f"cannot reach Ollama at {self.base_url} ({exc.__class__.__name__}). "
                "Is `ollama serve` running?"
            ) from exc
        if response.status_code == 404:
            raise ModelUnavailableError(f"Ollama returned 404 for {path}: {response.text[:300]}")
        if response.status_code >= 400:
            raise ModelUnavailableError(
                f"Ollama returned HTTP {response.status_code} for {path}: {response.text[:300]}"
            )
        data: dict[str, Any] = response.json()
        return data

    async def next_action(
        self,
        messages: list[dict[str, object]],
        schema: dict[str, object],
        config: ModelConfig,
    ) -> ModelResponse:
        options: dict[str, Any] = {
            "temperature": config.temperature,
            "num_ctx": config.num_ctx,
            "num_predict": config.num_predict,
        }
        if config.seed is not None:
            options["seed"] = config.seed
        body = {
            "model": config.model,
            "messages": messages,
            "stream": False,
            "think": config.think,
            "format": schema,
            "options": options,
        }
        started = time.monotonic()
        data = await self._request("POST", "/api/chat", config.request_timeout_seconds, body)
        latency = (time.monotonic() - started) * 1000
        message = data.get("message") or {}
        eval_ns = data.get("eval_duration")
        prompt_ns = data.get("prompt_eval_duration")
        inference_ms = None
        if isinstance(eval_ns, int):
            inference_ms = (eval_ns + (prompt_ns if isinstance(prompt_ns, int) else 0)) / 1e6
        return ModelResponse(
            content=str(message.get("content", "")),
            latency_ms=round(latency, 3),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            inference_ms=inference_ms,
            metadata={
                "done_reason": data.get("done_reason"),
                "thinking_present": bool(message.get("thinking")),
                "total_duration_ns": data.get("total_duration"),
                "load_duration_ns": data.get("load_duration"),
            },
        )

    async def version(self) -> str:
        data = await self._request("GET", "/api/version", 10)
        return str(data.get("version", "unknown"))

    async def list_models(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/tags", 10)
        models: list[dict[str, Any]] = data.get("models", [])
        return models

    async def show(self, model: str) -> dict[str, Any]:
        return await self._request("POST", "/api/show", 30, {"model": model})

    async def model_fingerprint(self, model: str) -> dict[str, Any]:
        """Full digest and details for an installed tag. Raises if absent."""
        for entry in await self.list_models():
            if entry.get("name") == model or entry.get("model") == model:
                details = entry.get("details", {})
                return {
                    "tag": model,
                    "digest": entry.get("digest"),
                    "size_bytes": entry.get("size"),
                    "modified_at": entry.get("modified_at"),
                    "family": details.get("family"),
                    "parameter_size": details.get("parameter_size"),
                    "quantization_level": details.get("quantization_level"),
                    "format": details.get("format"),
                }
        raise ModelUnavailableError(
            f"model {model!r} is not installed in Ollama. Run: ollama pull {model}"
        )
