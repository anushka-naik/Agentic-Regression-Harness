# ADR 0001: A structured action protocol and a small explicit loop

**Status:** accepted · **Date:** 2026-10-30

## Context

The agent under test must choose tools, pass arguments, read observations and answer.
Options were (a) each model's native function-calling template, (b) an agent framework,
or (c) a small JSON protocol owned by the harness.

## Decision

The model returns exactly one JSON object per request: either
`{"action": "tool_call", "tool_name", "arguments", "response": null}` or
`{"action": "final", "tool_name": null, "arguments": null, "response": {...}}`.
The schema is sent in Ollama's `format` field and validated again with Pydantic. The loop
(`src/agentcheck/agent/structured_agent.py`) is about 200 lines with explicit budgets.

Shape is constrained; answers are not. `tool_name` is a free string so a request for an
unregistered tool is *observable* and can be rejected and scored. `refund_issued` is a
plain boolean so a fabricated `true` stays detectable. The per-action null rules are in
the JSON schema (`anyOf`) because the first live probe showed Qwen3-4B filling
`tool_name` on a `final` action when they were only enforced in Python.

## Consequences

- Version 1 does not depend on a particular model's tool-call template, so prompts,
  models and decoding can be swapped one factor at a time.
- This is not native function calling; results say nothing about a model's native tool
  API. A native adapter can be added behind the same `ModelClient` interface.
- No orchestration framework: every step, retry and limit is visible in about 200 lines
  and in the trace.
