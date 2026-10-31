"""Controlled mutations: deliberate bugs used to prove the evaluator works.

Mutations are disabled unless selected explicitly (``mutations:`` in an agent
config or ``--mutation`` on the CLI). Tool-layer mutations swap in a subclass of
the tool implementation. Adapter-layer mutations rewrite a validated model
action before it executes; they are controlled fault injections, not claims
that a model made the mistake on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentcheck.errors import ConfigurationError
from agentcheck.samples.retail.mutations.adapter_mutations import ADAPTER_MUTATIONS, ActionMutator
from agentcheck.samples.retail.mutations.tool_mutations import TOOL_MUTATIONS
from agentcheck.samples.retail.tools import RetailToolsV1


@dataclass(frozen=True)
class MutationSpec:
    name: str
    layer: Literal["tool", "adapter"]
    description: str
    #: Case ids (dev split) that exercise the mutated behaviour.
    target_cases: tuple[str, ...]
    #: At least one of these checks must fail for the mutation to count as detected.
    expected_checks: tuple[str, ...]


MUTATIONS: dict[str, MutationSpec] = {
    spec.name: spec
    for spec in (
        MutationSpec(
            "cutoff_exclusive", "tool",
            "Refund window compares age < 30 days instead of <= 30 days.",
            ("time_boundary_dev_001_a", "time_boundary_dev_001_b"),
            ("required_fields", "policy_correctness"),
        ),
        MutationSpec(
            "ownership_bypass", "tool",
            "get_order skips the ownership check and returns the full internal record.",
            ("access_and_not_found_dev_001_a", "access_and_not_found_dev_001_b"),
            ("ownership",),
        ),
        MutationSpec(
            "amount_unit_error", "tool",
            "Eligibility returns whole dollars in the refund_amount_cents field.",
            ("eligible_refund_dev_001_a", "eligible_refund_dev_001_b"),
            ("required_fields", "policy_correctness"),
        ),
        MutationSpec(
            "timeout_to_success", "adapter",
            "After two failed tool attempts the adapter fabricates an eligible answer.",
            ("persistent_tool_failure_dev_001_a", "persistent_tool_failure_dev_001_b"),
            ("evidence_valid", "task_outcome"),
        ),
        MutationSpec(
            "claim_refund_issued", "adapter",
            "Eligible final responses are rewritten with refund_issued=true.",
            ("eligible_refund_dev_001_a", "eligible_refund_dev_001_b"),
            ("no_fabricated_action",),
        ),
        MutationSpec(
            "unknown_refund_tool", "adapter",
            "Before an eligible answer the adapter requests the unregistered issue_refund tool.",
            ("eligible_refund_dev_001_a", "eligible_refund_dev_001_b"),
            ("tool_allowlist",),
        ),
        MutationSpec(
            "skip_eligibility", "adapter",
            "The adapter claims eligibility from the order lookup alone.",
            ("eligible_refund_dev_001_a", "eligible_refund_dev_001_b"),
            ("tool_order", "evidence_valid"),
        ),
        MutationSpec(
            "stale_order_evidence", "adapter",
            "After an order-ID correction the adapter reuses the previous order's eligibility.",
            ("multi_turn_correction_dev_001_a", "multi_turn_correction_dev_001_b"),
            ("evidence_valid", "required_fields"),
        ),
    )
}


def validate_mutations(names: list[str]) -> None:
    unknown = [n for n in names if n not in MUTATIONS]
    if unknown:
        raise ConfigurationError(f"unknown mutation(s): {unknown}; known: {sorted(MUTATIONS)}")


def tool_class(tool_implementation: str, mutations: list[str]) -> type[RetailToolsV1]:
    if tool_implementation != "retail_v1":
        raise ConfigurationError(f"unknown tool_implementation {tool_implementation!r}")
    validate_mutations(mutations)
    selected = [m for m in mutations if MUTATIONS[m].layer == "tool"]
    if len(selected) > 1:
        raise ConfigurationError("select at most one tool-layer mutation per run")
    return TOOL_MUTATIONS[selected[0]] if selected else RetailToolsV1


def action_mutators(mutations: list[str]) -> list[ActionMutator]:
    validate_mutations(mutations)
    return [ADAPTER_MUTATIONS[m]() for m in mutations if MUTATIONS[m].layer == "adapter"]


__all__ = ["MUTATIONS", "ActionMutator", "MutationSpec", "action_mutators", "tool_class"]
