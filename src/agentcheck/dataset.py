"""Loading and validating versioned case datasets."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from agentcheck.canonical import sha256_file, sha256_json
from agentcheck.errors import DatasetError
from agentcheck.evaluators import retail_oracle
from agentcheck.schemas.cases import (
    CATEGORIES,
    EvaluationCase,
    LoadedCase,
    branches,
)
from agentcheck.schemas.domain import World
from agentcheck.tools.dispatcher import REGISTERED_TOOLS

SPLIT_FILES = {"dev": "cases/dev.jsonl", "validation": "cases/validation.jsonl",
               "heldout": "cases/heldout.jsonl"}
SUITES = ("smoke", "dev", "validation", "heldout", "all")
EXPECTED_SPLIT_COUNTS = {"dev": 72, "validation": 24, "heldout": 24}
CASES_PER_CATEGORY = 10
FAMILIES_PER_CATEGORY = 5


@dataclass
class Dataset:
    root: Path
    manifest: dict[str, Any]
    cases: list[EvaluationCase]
    worlds: dict[str, World]

    @property
    def content_sha256(self) -> str:
        return str(self.manifest["content_sha256"])

    def by_id(self) -> dict[str, EvaluationCase]:
        return {c.case_id: c for c in self.cases}

    def load_case(self, case: EvaluationCase) -> LoadedCase:
        world = self.worlds[case.world_file]
        return LoadedCase(case=case, world=world, world_sha256=sha256_json(world))

    def suite(self, name: str) -> list[LoadedCase]:
        if name not in SUITES:
            raise DatasetError(f"unknown suite {name!r}; choose one of {SUITES}")
        if name == "all":
            selected = self.cases
        elif name == "smoke":
            index = self.by_id()
            selected = [index[cid] for cid in self.manifest["smoke_case_ids"]]
        else:
            selected = [c for c in self.cases if c.split == name]
        return [self.load_case(c) for c in selected]

    def select(self, case_ids: list[str]) -> list[LoadedCase]:
        index = self.by_id()
        missing = [c for c in case_ids if c not in index]
        if missing:
            raise DatasetError(f"unknown case ids: {missing}")
        return [self.load_case(index[c]) for c in case_ids]


def _safe_join(root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise DatasetError(f"path escapes dataset directory: {relative}")
    path = (root / rel).resolve()
    if root.resolve() not in path.parents and path != root.resolve():
        raise DatasetError(f"path escapes dataset directory: {relative}")
    return path


def read_cases(path: Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(EvaluationCase.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DatasetError(f"{path}:{line_no}: invalid case: {exc}") from exc
    return cases


def load_dataset(root: Path) -> Dataset:
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise DatasetError(
            f"no manifest at {manifest_path}. Generate it with "
            "`uv run python scripts/generate_dataset.py --seed 42`"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases: list[EvaluationCase] = []
    for rel in SPLIT_FILES.values():
        cases.extend(read_cases(_safe_join(root, rel)))
    worlds: dict[str, World] = {}
    for case in cases:
        if case.world_file not in worlds:
            path = _safe_join(root, case.world_file)
            try:
                worlds[case.world_file] = World.model_validate_json(path.read_text("utf-8"))
            except FileNotFoundError as exc:
                raise DatasetError(f"{case.case_id}: world file missing: {case.world_file}") from exc
            except ValidationError as exc:
                raise DatasetError(f"invalid world {case.world_file}: {exc}") from exc
    return Dataset(root=root, manifest=manifest, cases=cases, worlds=worlds)


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _check_oracle(loaded: LoadedCase, report: ValidationReport) -> None:
    case = loaded.case
    customer = case.session.customer_id
    for turn_index, expected in enumerate(case.expected_turns, start=1):
        for b_index, branch in enumerate(branches(expected), start=1):
            where = f"{case.case_id} turn {turn_index} branch {b_index}"
            fields = branch.fields
            order_id = fields.get("order_id")
            elig_args = branch.expected_arguments.get("check_refund_eligibility")
            if "eligible" in fields and fields["eligible"] is not None:
                if not elig_args:
                    report.errors.append(f"{where}: eligibility expected without eligibility arguments")
                    continue
                try:
                    decision = retail_oracle.decide(
                        loaded.world, customer, elig_args["order_id"], elig_args["reason"],
                        case.clock_utc,
                    )
                except retail_oracle.FixtureError as exc:
                    report.errors.append(f"{where}: {exc}")
                    continue
                oracle = {
                    "eligible": decision.eligible, "reason_code": decision.reason_code,
                    "refund_amount_cents": decision.refund_amount_cents,
                }
                for key, value in oracle.items():
                    if key in fields and fields[key] != value:
                        report.errors.append(
                            f"{where}: expected {key}={fields[key]!r} contradicts oracle {value!r}"
                        )
            if fields.get("order_status") is not None and order_id:
                order = retail_oracle.accessible_order(loaded.world, customer, order_id)
                if order is None or order.status != fields["order_status"]:
                    report.errors.append(f"{where}: order_status contradicts oracle")
            if (
                branch.outcomes == ["not_found"] and order_id
                and retail_oracle.accessible_order(loaded.world, customer, order_id) is not None
            ):
                report.errors.append(f"{where}: not_found expected but order is accessible")
            for tool in [*branch.required_tools_in_order, *branch.expected_arguments]:
                if tool not in REGISTERED_TOOLS:
                    report.errors.append(f"{where}: required tool {tool!r} is not registered")
            if fields.get("refund_issued") is not False:
                report.errors.append(f"{where}: every branch must expect refund_issued=false")
    for entry in case.fault_plan:
        if entry.tool_name not in REGISTERED_TOOLS:
            report.errors.append(f"{case.case_id}: fault plan targets unknown tool {entry.tool_name}")
    for order in loaded.world.orders:
        if order.status == "delivered" and order.delivered_at is not None:
            try:
                age = retail_oracle.epoch_seconds(case.clock_utc) - retail_oracle.epoch_seconds(
                    order.delivered_at
                )
            except retail_oracle.FixtureError as exc:
                report.errors.append(f"{case.case_id}: {exc}")
                continue
            if age < 0:
                report.errors.append(f"{case.case_id}: {order.order_id} has negative delivery age")


def validate_dataset(root: Path) -> ValidationReport:
    report = ValidationReport()
    try:
        dataset = load_dataset(root)
    except DatasetError as exc:
        report.errors.append(str(exc))
        return report

    cases = dataset.cases
    manifest = dataset.manifest

    ids = [c.case_id for c in cases]
    for cid, n in Counter(ids).items():
        if n > 1:
            report.errors.append(f"duplicate case_id {cid}")

    split_counts: Counter[str] = Counter(c.split for c in cases)
    for split, want in EXPECTED_SPLIT_COUNTS.items():
        if split_counts.get(split, 0) != want:
            report.errors.append(f"split {split}: {split_counts.get(split, 0)} cases, expected {want}")
    category_counts: Counter[str] = Counter(c.category for c in cases)
    for category in CATEGORIES:
        if category_counts.get(category, 0) != CASES_PER_CATEGORY:
            report.errors.append(
                f"category {category}: {category_counts.get(category, 0)} cases, expected 10"
            )

    families: dict[str, list[EvaluationCase]] = defaultdict(list)
    for c in cases:
        families[c.scenario_family_id].append(c)
    per_category_split: dict[tuple[str, str], set[str]] = defaultdict(set)
    for fam, members in families.items():
        if len(members) != 2:
            report.errors.append(f"family {fam} has {len(members)} variants, expected 2")
        if len({m.split for m in members}) != 1:
            report.errors.append(f"family {fam} spans splits {sorted({m.split for m in members})}")
        if len({m.category for m in members}) != 1:
            report.errors.append(f"family {fam} spans categories")
        if len({m.semantic_parent_id for m in members}) != 1:
            report.errors.append(f"family {fam} has inconsistent semantic_parent_id")
        texts = [tuple(s.user for s in m.dialogue) for m in members]
        if len(set(texts)) != len(texts):
            report.errors.append(f"family {fam} variants have identical wording")
        per_category_split[(members[0].category, members[0].split)].add(fam)
    for category in CATEGORIES:
        for split, want in (("dev", 3), ("validation", 1), ("heldout", 1)):
            have = len(per_category_split[(category, split)])
            if have != want:
                report.errors.append(f"{category}/{split}: {have} families, expected {want}")

    parents: dict[str, set[str]] = defaultdict(set)
    for c in cases:
        parents[c.semantic_parent_id].add(c.split)
    for parent, splits in parents.items():
        if len(splits) > 1:
            report.errors.append(f"semantic parent {parent} spans splits {sorted(splits)} (leakage)")

    smoke = manifest.get("smoke_case_ids", [])
    index = dataset.by_id()
    if len(smoke) != len(CATEGORIES):
        report.errors.append(f"smoke suite has {len(smoke)} cases, expected {len(CATEGORIES)}")
    smoke_categories = set()
    for cid in smoke:
        if cid not in index:
            report.errors.append(f"smoke case {cid} does not exist")
        elif index[cid].split != "dev":
            report.errors.append(f"smoke case {cid} is not a dev case")
        else:
            smoke_categories.add(index[cid].category)
    if smoke and smoke_categories != set(CATEGORIES):
        report.errors.append("smoke suite must contain one case per category")

    for c in cases:
        _check_oracle(dataset.load_case(c), report)
        if c.review_status == "human_reviewed":
            report.warnings.append(f"{c.case_id} claims human review; confirm review.csv evidence")

    for rel, digest in manifest.get("files", {}).items():
        path = _safe_join(root, rel)
        if not path.exists():
            report.errors.append(f"manifest lists missing file {rel}")
        elif sha256_file(path) != digest:
            report.errors.append(f"hash mismatch for {rel} (file changed after manifest)")
    recomputed = sha256_json(manifest.get("files", {}))
    if manifest.get("content_sha256") != recomputed:
        report.errors.append("manifest content_sha256 does not match its file hashes")

    report.stats = {
        "cases": len(cases), "families": len(families), "splits": dict(split_counts),
        "categories": dict(category_counts), "worlds": len(dataset.worlds),
        "content_sha256": manifest.get("content_sha256"),
    }
    return report
