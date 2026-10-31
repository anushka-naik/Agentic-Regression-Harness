# ADR 0002: Deterministic checks gate releases; LLM review is advisory

**Status:** accepted · **Date:** 2026-10-30

## Context

A CI gate must be reproducible, explainable and hard to game. LLM judges are useful for
reviewing free text but are stochastic, can share failure modes with the actor (here
both are Qwen3), and are uncalibrated until humans label a sample.

## Decision

- The gate is computed only from deterministic checks over structured fields and traces
  (`evaluators/deterministic.py`) plus an independent reference oracle.
- The Qwen3-8B judge reviews the `message` text and produces `advisory_pass`,
  `advisory_fail` or `needs_review`. A malformed or missing judge result is
  `needs_review`, never a pass. Judge output never changes a deterministic result.
- Agreement with humans is reported only from labels a person entered, with reviewer and
  date. Until then the CLI prints `Calibration pending: no verified human labels`.

## Consequences

- A green gate means the structured behaviour matched; reports say explicitly when
  natural-language accuracy was not reviewed.
- Some message-level errors (for example a wrong amount only in prose) are invisible to
  the gate and surface only as advisory findings or through the small phrase canary in
  `no_fabricated_action`.
