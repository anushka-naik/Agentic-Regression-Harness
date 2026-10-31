"""The structured-action agent loop.

One model request yields one JSON action. Tool calls go through the dispatcher;
a final response ends the turn and is handed to the scripted user, who either
supplies the next message or ends the case. Every request, response, protocol
error, repair and tool event is traced.

Budget violations raise :class:`AgentLimitExceeded` (an agent failure); provider
failures raise :class:`InfrastructureError` (not an agent failure).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agentcheck.errors import AgentLimitExceeded
from agentcheck.model_clients.base import ModelClient, ModelConfig
from agentcheck.runner.trace import TraceSink
from agentcheck.samples.retail.mutations import ActionMutator
from agentcheck.schemas.cases import AgentCaseInput
from agentcheck.schemas.config import AgentConfig
from agentcheck.schemas.protocol import AgentAction, FinalResponse, action_json_schema
from agentcheck.tools.dispatcher import ToolDispatcher

#: Rough characters-per-token used for the pre-flight context budget check.
CHARS_PER_TOKEN = 3.2


@dataclass
class AdapterOutcome:
    turns_started: int
    finals: list[FinalResponse]


def _summarize_validation(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors()]
        return "; ".join(parts)[:800]
    return str(exc)[:800]


class StructuredActionAgent:
    def __init__(
        self,
        client: ModelClient,
        system_prompt: str,
        model_config: ModelConfig,
        mutators: list[ActionMutator] | None = None,
    ) -> None:
        self.client = client
        self.system_prompt = system_prompt
        self.model_config = model_config
        self.mutators = mutators or []
        self.schema = action_json_schema()

    def build_messages(
        self, session_context: dict[str, Any], history: list[dict[str, Any]],
        format_error: str | None,
    ) -> list[dict[str, object]]:
        envelope: dict[str, Any] = {"session": session_context, "conversation": history}
        if format_error is not None:
            envelope["format_error"] = (
                "Your previous reply was not a valid action: "
                f"{format_error}. Reply with one valid JSON action."
            )
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": json.dumps(envelope, ensure_ascii=False)},
        ]

    def _check_context(self, messages: list[dict[str, object]], config: AgentConfig) -> int:
        chars = sum(len(str(m["content"])) for m in messages)
        estimate = int(chars / CHARS_PER_TOKEN) + 1
        budget = config.num_ctx - config.num_predict
        if estimate > budget:
            raise AgentLimitExceeded(
                "context_budget_exceeded",
                f"estimated {estimate} prompt tokens exceeds budget {budget}; refusing to truncate",
            )
        return estimate

    async def run_case(
        self,
        case_input: AgentCaseInput,
        tools: ToolDispatcher,
        trace: TraceSink,
        config: AgentConfig,
    ) -> AdapterOutcome:
        history: list[dict[str, Any]] = []
        finals: list[FinalResponse] = []
        calls_in_case = 0
        turn = 0
        message: str | None = case_input.user.first_message()

        while message is not None:
            turn += 1
            if turn > config.max_user_turns:
                raise AgentLimitExceeded("max_user_turns", f"more than {config.max_user_turns} turns")
            trace.emit("user_message", {"turn": turn, "text": message})
            history.append({"type": "user_message", "text": message})

            calls_in_turn = 0
            tool_attempts = 0
            repairs = 0
            format_error: str | None = None
            final: FinalResponse | None = None

            while final is None:
                if calls_in_turn >= config.max_model_calls_per_turn:
                    raise AgentLimitExceeded("model_call_limit_turn", f"turn {turn}")
                if calls_in_case >= config.max_model_calls_per_case:
                    raise AgentLimitExceeded("model_call_limit_case", f"turn {turn}")
                messages = self.build_messages(case_input.session_context, history, format_error)
                estimate = self._check_context(messages, config)
                calls_in_turn += 1
                calls_in_case += 1
                trace.emit("model_request", {
                    "turn": turn, "call_index": calls_in_case, "repair": format_error is not None,
                    "estimated_prompt_tokens": estimate, "messages": messages,
                })
                response = await self.client.next_action(messages, self.schema, self.model_config)
                trace.emit("model_response", {
                    "turn": turn, "call_index": calls_in_case, "repair": format_error is not None,
                    "content": response.content, "latency_ms": response.latency_ms,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "inference_ms": response.inference_ms, "metadata": response.metadata,
                })
                try:
                    action = AgentAction.model_validate_json(response.content)
                except ValidationError as exc:
                    summary = _summarize_validation(exc)
                    trace.emit("protocol_error", {
                        "turn": turn, "call_index": calls_in_case, "error": summary,
                        "raw": response.content[:4000], "repair_attempt": format_error is not None,
                    })
                    if repairs >= config.format_repair_attempts_per_turn:
                        raise AgentLimitExceeded("protocol_error", summary) from exc
                    repairs += 1
                    format_error = summary
                    trace.emit("repair_requested", {"turn": turn, "error": summary})
                    continue
                format_error = None

                applied: list[str] = []
                for mutator in self.mutators:
                    mutated = mutator.transform(action, history)
                    if mutated is not action:
                        applied.append(mutator.name)
                        action = mutated

                history.append({
                    "type": "assistant_action", "action": action.model_dump(mode="json")
                })
                if action.action == "tool_call":
                    if tool_attempts >= config.max_tool_attempts_per_turn:
                        raise AgentLimitExceeded("tool_attempt_limit", f"turn {turn}")
                    tool_attempts += 1
                    observation = tools.dispatch(
                        str(action.tool_name), action.arguments, turn,
                        mutation=applied or None,
                    )
                    history.append({"type": "tool_observation", **observation})
                else:
                    assert action.response is not None
                    final = action.response
                    trace.emit("assistant_final", {
                        "turn": turn, "response": final.model_dump(mode="json"),
                        "mutation": applied or None,
                    })
            finals.append(final)
            message = case_input.user.next_message(final)
        return AdapterOutcome(turns_started=turn, finals=finals)
