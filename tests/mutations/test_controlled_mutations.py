"""Each controlled mutation: control passes, mutation changes behaviour, a targeted check fails."""

from __future__ import annotations

import pytest

from agentcheck.comparisons.metrics import first_divergence
from agentcheck.model_clients.scripted import ReferencePolicyClient
from agentcheck.runner.execution import execute_trial
from agentcheck.samples.retail.mutations import MUTATIONS
from tests.conftest import PROMPT, scripted_config


def test_suite_has_eight_mutations() -> None:
    assert len(MUTATIONS) == 8
    assert sum(m.layer == "tool" for m in MUTATIONS.values()) == 3


@pytest.mark.parametrize("name", sorted(MUTATIONS))
async def test_mutation_detected_with_passing_control(dataset, name: str) -> None:  # type: ignore[no-untyped-def]
    spec = MUTATIONS[name]
    for loaded in dataset.select(list(spec.target_cases)):
        control = await execute_trial(loaded, scripted_config(), ReferencePolicyClient(), PROMPT,
                                      repeat_index=1, seed=42)
        mutated = await execute_trial(loaded, scripted_config(mutations=[name]), ReferencePolicyClient(),
                                      PROMPT, repeat_index=1, seed=42)
        assert control.status == "passed"
        assert first_divergence(control, mutated) is not None, "mutation did not change behaviour"
        targeted = {c.code for c in mutated.failed_checks} & set(spec.expected_checks)
        assert targeted, f"{name} on {loaded.case.case_id}: failed {[c.code for c in mutated.failed_checks]}"
        assert mutated.mutation_source == f"{spec.layer}:{name}"


async def test_mutations_do_not_break_unrelated_cases(dataset) -> None:  # type: ignore[no-untyped-def]
    unrelated = dataset.select(["order_status_dev_001_a"])[0]
    trial = await execute_trial(unrelated, scripted_config(mutations=["cutoff_exclusive"]),
                                ReferencePolicyClient(), PROMPT, repeat_index=1, seed=42)
    assert trial.status == "passed"
