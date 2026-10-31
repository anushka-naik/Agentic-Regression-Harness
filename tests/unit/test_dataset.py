from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter

import pytest

from agentcheck.dataset import validate_dataset
from agentcheck.model_clients.scripted import ReferencePolicyClient
from agentcheck.runner.execution import execute_trial
from agentcheck.schemas.cases import CATEGORIES, AgentCaseInput
from tests.conftest import DATASET, PROMPT, ROOT, scripted_config


def generator():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("generate_dataset", ROOT / "scripts" / "generate_dataset.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_dataset"] = module
    spec.loader.exec_module(module)
    return module


def test_committed_dataset_validates() -> None:
    report = validate_dataset(DATASET)
    assert report.ok, report.errors
    assert report.stats["splits"] == {"dev": 72, "validation": 24, "heldout": 24}
    assert set(report.stats["categories"].values()) == {10}


def test_generator_is_byte_identical_and_matches_committed_files() -> None:
    gen = generator()
    first, second = gen.render_files(42), gen.render_files(42)
    assert first == second
    for rel, data in first.items():
        assert (DATASET / rel).read_bytes() == data, f"{rel} differs from the generator output"


def test_different_seed_changes_fixtures_not_structure() -> None:
    gen = generator()
    other = json.loads(gen.render_files(7)["manifest.json"])
    assert other["split_counts"] == {"dev": 72, "validation": 24, "heldout": 24}
    assert other["content_sha256"] != json.loads((DATASET / "manifest.json").read_text())["content_sha256"]


def test_families_never_split_and_parents_stay_together(dataset) -> None:  # type: ignore[no-untyped-def]
    families: dict[str, set[str]] = {}
    parents: dict[str, set[str]] = {}
    for c in dataset.cases:
        families.setdefault(c.scenario_family_id, set()).add(c.split)
        parents.setdefault(c.semantic_parent_id, set()).add(c.split)
    assert all(len(s) == 1 for s in families.values())
    assert all(len(s) == 1 for s in parents.values())
    per_split = Counter((c.category, c.split) for c in dataset.cases)
    for cat in CATEGORIES:
        assert (per_split[(cat, "dev")], per_split[(cat, "validation")], per_split[(cat, "heldout")]) == (6, 2, 2)


def test_smoke_suite_is_twelve_dev_cases_one_per_category(dataset) -> None:  # type: ignore[no-untyped-def]
    smoke = dataset.suite("smoke")
    assert len(smoke) == 12
    assert {c.case.category for c in smoke} == set(CATEGORIES)
    assert all(c.case.split == "dev" for c in smoke)


def test_no_case_claims_human_review(dataset) -> None:  # type: ignore[no-untyped-def]
    assert {c.review_status for c in dataset.cases} == {"machine_checked"}
    review = (DATASET / "review.csv").read_text().splitlines()[1:]
    assert all(row.split(",")[5:7] == ["", ""] for row in review)


def test_seed_fixture_rows_are_exact(dataset) -> None:  # type: ignore[no-untyped-def]
    orders = {o.order_id: o for o in dataset.worlds["worlds/seed_store.json"].orders}
    assert orders["O-1002"].private_marker == "PRIVATE_C002_7F31" and orders["O-1002"].customer_id == "C-002"
    assert orders["O-1003"].delivered_at == "2026-01-01T12:00:00Z"
    assert orders["O-1004"].delivered_at == "2026-01-01T11:59:59Z"
    assert orders["O-1005"].final_sale and orders["O-1006"].status == "shipped"


def test_agent_case_input_carries_no_expectations() -> None:
    assert set(AgentCaseInput.model_fields) == {"session_context", "user"}


def test_validator_flags_tampered_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import shutil

    copy = tmp_path / "retail"
    shutil.copytree(DATASET, copy)
    path = copy / "cases" / "dev.jsonl"
    path.write_text(path.read_text().replace('"refund_amount_cents":12900', '"refund_amount_cents":12000', 1))
    report = validate_dataset(copy)
    assert not report.ok
    assert any("contradicts oracle" in e for e in report.errors)
    assert any("hash mismatch" in e for e in report.errors)


@pytest.mark.parametrize("split", ["dev", "validation", "heldout"])
async def test_reference_policy_satisfies_every_case(dataset, split: str) -> None:  # type: ignore[no-untyped-def]
    """Every expectation is reachable by honest tool use (fixture mode)."""
    failures = []
    for loaded in dataset.suite(split):
        trial = await execute_trial(loaded, scripted_config(), ReferencePolicyClient(), PROMPT,
                                    repeat_index=1, seed=42)
        if trial.status != "passed":
            failures.append((loaded.case.case_id, [c.message for c in trial.failed_checks]))
    assert not failures
