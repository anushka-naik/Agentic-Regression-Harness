from __future__ import annotations

import csv
from pathlib import Path

from agentcheck.errors import ModelUnavailableError
from agentcheck.evaluators.calibration import (
    PENDING_MESSAGE,
    REVIEW_FIELDS,
    calibrate,
    calibration_metrics,
    import_labels,
    wilson,
)
from agentcheck.evaluators.judge import Judge, JudgeConfig
from agentcheck.model_clients.scripted import ScriptedModelClient
from tests.conftest import ROOT

CFG = JudgeConfig(judge_id="t", provider="scripted", model="m")


async def test_malformed_judge_output_needs_review() -> None:
    result = await Judge(ScriptedModelClient(["not json"]), CFG, "rubric").review({"message": "x"})
    assert result["verdict"] is None and result["semantic_review_status"] == "needs_review"


async def test_judge_infrastructure_error_needs_review() -> None:
    class Down:
        provider = "ollama"

        async def next_action(self, *a):  # type: ignore[no-untyped-def]
            raise ModelUnavailableError("down")

    result = await Judge(Down(), CFG, "rubric").review({"message": "x"})
    assert result["semantic_review_status"] == "needs_review"


async def test_valid_verdicts_map_to_advisory_statuses() -> None:
    client = ScriptedModelClient([
        '{"verdict":"fail","failure_types":["wrong_amount"],"evidence_event_ids":["tool-0002"],"explanation":"x"}',
        '{"verdict":"uncertain","failure_types":[],"evidence_event_ids":[],"explanation":"y"}',
    ])
    judge = Judge(client, CFG, "rubric")
    assert (await judge.review({}))["semantic_review_status"] == "advisory_fail"
    assert (await judge.review({}))["semantic_review_status"] == "needs_review"


def test_wilson_interval() -> None:
    assert wilson(0, 0) is None
    lo, hi = wilson(8, 10)  # type: ignore[misc]
    assert 0.49 < lo < 0.5 and 0.94 < hi < 0.95


def test_metrics_on_manually_labelled_fixture() -> None:
    labels = {"r1": "flawed", "r2": "flawed", "r3": "flawed", "r4": "acceptable", "r5": "acceptable",
              "r6": "acceptable", "r7": "flawed"}
    verdicts = {"r1": "fail", "r2": "fail", "r3": "pass", "r4": "pass", "r5": "fail", "r6": "pass",
                "r7": "uncertain"}
    m = calibration_metrics(labels, verdicts)
    assert m["confusion_matrix"] == {"tp": 2, "fp": 1, "tn": 2, "fn": 1}
    assert m["uncertain"] == 1 and m["coverage"]["value"] == round(6 / 7, 4)
    assert m["precision"]["value"] == round(2 / 3, 4) and m["recall"]["value"] == round(2 / 3, 4)
    assert m["false_positive_rate"]["value"] == round(1 / 3, 4)
    assert m["balanced_accuracy"] == round((2 / 3 + 2 / 3) / 2, 4)


def test_zero_denominators_are_na() -> None:
    m = calibration_metrics({"r1": "flawed"}, {"r1": "uncertain"})
    assert m["precision"]["value"] is None and m["balanced_accuracy"] is None


def test_unreviewed_labels_never_produce_agreement() -> None:
    report = calibrate(ROOT / "data" / "judge_v1" / "review.csv", {"x": "pass"})
    assert report["status"] == "pending" and report["message"] == PENDING_MESSAGE


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=REVIEW_FIELDS)
        w.writeheader()
        w.writerows(rows)


def test_labels_import_requires_reviewer_metadata(tmp_path: Path) -> None:
    target, source = tmp_path / "review.csv", tmp_path / "in.csv"
    base = {"trace_id": "t", "family_id": "f", "proposed_label": "flawed", "human_label": "",
            "reviewer_id": "", "reviewed_at": "", "notes": ""}
    _write(target, [{**base, "response_id": "a"}, {**base, "response_id": "b"}])
    _write(source, [
        {**base, "response_id": "a", "human_label": "flawed", "reviewer_id": "rev1",
         "reviewed_at": "2026-10-30T10:00:00"},
        {**base, "response_id": "b", "human_label": "flawed", "reviewer_id": "", "reviewed_at": ""},
    ])
    assert import_labels(source, target) == {"accepted": 1, "rejected": 1}
    report = calibrate(target, {"a": "fail", "b": "fail"})
    assert report["status"] == "reviewed" and report["reviewed_rows"] == 1 and report["single_reviewer"]
    assert report["judge_remains_advisory"] is True
