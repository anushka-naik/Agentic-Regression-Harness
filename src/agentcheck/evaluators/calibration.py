"""Judge calibration against *verified human labels only*.

A flawed response is the positive class. Rows without a human label, reviewer
and review date are excluded; if none remain, calibration is pending and no
agreement number is produced. Uncertain or malformed judge outputs are counted
separately and reported with their coverage, never silently dropped.
"""

from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path
from typing import Any

PENDING_MESSAGE = "Calibration pending: no verified human labels"
REVIEW_FIELDS = ["trace_id", "response_id", "family_id", "proposed_label", "human_label",
                 "reviewer_id", "reviewed_at", "notes"]
HUMAN_LABELS = ("acceptable", "flawed")


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def read_reviews(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def verified(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    out = {}
    for row in rows:
        label = (row.get("human_label") or "").strip()
        if label in HUMAN_LABELS and (row.get("reviewer_id") or "").strip() and (row.get("reviewed_at") or "").strip():
            out[row["response_id"]] = row
    return out


def _rate(num: int, den: int) -> dict[str, Any]:
    return {"value": None if den == 0 else round(num / den, 4), "numerator": num, "denominator": den,
            "wilson_95": wilson(num, den)}


def calibration_metrics(labels: dict[str, str], verdicts: dict[str, str | None]) -> dict[str, Any]:
    """``labels``: response_id -> acceptable|flawed. ``verdicts``: response_id -> pass|fail|uncertain|None."""
    tp = fp = tn = fn = uncertain = missing = 0
    for rid, label in labels.items():
        verdict = verdicts.get(rid)
        if rid not in verdicts:
            missing += 1
            continue
        if verdict not in ("pass", "fail"):
            uncertain += 1
            continue
        flagged = verdict == "fail"
        if label == "flawed":
            tp += flagged
            fn += not flagged
        else:
            fp += flagged
            tn += not flagged
    decided = tp + fp + tn + fn
    n = len(labels)
    recall = _rate(tp, tp + fn)
    fpr = _rate(fp, fp + tn)
    balanced = None
    if recall["value"] is not None and fpr["value"] is not None:
        balanced = round((recall["value"] + (1 - fpr["value"])) / 2, 4)
    return {
        "labelled": n,
        "decided": decided,
        "uncertain": uncertain,
        "missing_judge_result": missing,
        "coverage": _rate(decided, n),
        "uncertain_rate": _rate(uncertain, n),
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "precision": _rate(tp, tp + fp),
        "recall": recall,
        "false_positive_rate": fpr,
        "false_negative_rate": _rate(fn, tp + fn),
        "balanced_accuracy": balanced,
        "note": "Computed on decided responses only; see coverage and uncertain_rate.",
    }


def calibrate(review_path: Path, verdicts: dict[str, str | None],
              targets: dict[str, float] | None = None) -> dict[str, Any]:
    rows = read_reviews(review_path)
    reviewed = verified(rows)
    reviewers = sorted({r["reviewer_id"] for r in reviewed.values()})
    if not reviewed:
        return {"status": "pending", "message": PENDING_MESSAGE, "rows": len(rows),
                "judge_verdicts": _distribution(verdicts)}
    for row in reviewed.values():
        datetime.fromisoformat(row["reviewed_at"])
    metrics = calibration_metrics({k: v["human_label"] for k, v in reviewed.items()}, verdicts)
    targets = targets or {"balanced_accuracy_min": 0.85, "false_positive_rate_max": 0.10}
    met = (
        metrics["balanced_accuracy"] is not None
        and metrics["balanced_accuracy"] >= targets["balanced_accuracy_min"]
        and metrics["false_positive_rate"]["value"] is not None
        and metrics["false_positive_rate"]["value"] <= targets["false_positive_rate_max"]
    )
    return {
        "status": "reviewed", "reviewed_rows": len(reviewed), "total_rows": len(rows),
        "reviewers": reviewers,
        "single_reviewer": len(reviewers) == 1,
        "metrics": metrics, "exploratory_targets": targets, "targets_met": met,
        "judge_remains_advisory": True,
        "judge_verdicts": _distribution(verdicts),
    }


def _distribution(verdicts: dict[str, str | None]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in verdicts.values():
        key = v or "needs_review"
        out[key] = out.get(key, 0) + 1
    return out


def import_labels(source: Path, target: Path) -> dict[str, int]:
    """Merge reviewed labels from ``source`` into the review file ``target``.

    Only rows with a valid label, reviewer and ISO timestamp are accepted.
    """
    incoming = read_reviews(source)
    current = {r["response_id"]: r for r in read_reviews(target)}
    accepted = rejected = 0
    for row in incoming:
        rid = row.get("response_id", "")
        label = (row.get("human_label") or "").strip()
        reviewer = (row.get("reviewer_id") or "").strip()
        when = (row.get("reviewed_at") or "").strip()
        try:
            valid = rid in current and label in HUMAN_LABELS and bool(reviewer) and bool(datetime.fromisoformat(when))
        except ValueError:
            valid = False
        if not valid:
            rejected += 1
            continue
        current[rid].update(human_label=label, reviewer_id=reviewer, reviewed_at=when,
                            notes=row.get("notes", current[rid].get("notes", "")))
        accepted += 1
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in current.values():
            writer.writerow({k: row.get(k, "") for k in REVIEW_FIELDS})
    return {"accepted": accepted, "rejected": rejected}
