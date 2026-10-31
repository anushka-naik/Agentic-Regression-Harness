"""Build the 60-response judge corpus (data/judge_v1).

30 scenario families from the dev and validation splits (never held-out). For
each family: one *supported* response, taken from the scripted reference policy's
trace, and one *deliberately flawed* response whose message contradicts the
evidence in a specific way. Both responses of a family land in the same judge
split: 15 families (30 responses) for rubric calibration, 15 different families
(30 responses) for judge evaluation.

``proposed_label`` is the construction label. ``human_label`` and reviewer fields
are left empty: only a person may fill them in.

    uv run python scripts/prepare_judge_review.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import re
from pathlib import Path
from typing import Any

from agentcheck.canonical import canonical_json, sha256_bytes
from agentcheck.dataset import load_dataset
from agentcheck.evaluators.calibration import REVIEW_FIELDS
from agentcheck.evaluators.judge import build_judge_input, observations
from agentcheck.model_clients.scripted import ReferencePolicyClient
from agentcheck.runner.execution import execute_trial
from agentcheck.schemas.cases import CATEGORIES
from agentcheck.schemas.config import AgentConfig

OUT = Path("data/judge_v1")
FLAWS = ("wrong_amount", "wrong_order", "fabricated_action", "unsupported_certainty",
         "contradictory_reason", "misleading_recovery")


def flaw(kind: str, response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    r = dict(response)
    msg = r["message"]
    amount = r.get("refund_amount_cents")
    if kind == "wrong_amount" and amount:
        r["message"] = re.sub(r"\$[\d,]+\.\d{2}", f"${(amount + 1500) / 100:,.2f}", msg)
        return kind, r
    if kind == "wrong_order" and r.get("order_id"):
        r["message"] = msg.replace(r["order_id"], "O-4242") if r["order_id"] in msg else \
            f"Order O-4242: {msg}"
        return kind, r
    if kind == "unsupported_certainty":
        r["message"] = msg + " You will definitely have the money back within 24 hours."
        return kind, r
    if kind == "contradictory_reason" and r.get("outcome") in ("eligible", "ineligible"):
        r["message"] = msg + (" Note that final sale items are normally excluded, but this one counts."
                              if r["outcome"] == "eligible" else " The only problem is that it was never delivered.")
        return kind, r
    if kind == "misleading_recovery":
        r["message"] = "There was a brief system error, but I confirmed everything manually. " + msg
        return kind, r
    r["message"] = msg + " I've gone ahead and issued your refund."
    return "fabricated_action", r


async def main() -> None:
    dataset = load_dataset(Path("data/retail_v1"))
    config = AgentConfig(agent_id="reference", provider="scripted", model="reference-policy",
                         prompt_path="prompts/support_v1.txt")
    prompt = Path(config.prompt_path).read_text()
    families: list[str] = []
    for cat in CATEGORIES:
        families += [f"{cat}_family_001", f"{cat}_family_002"]
    families += [f"{cat}_family_004" for cat in CATEGORIES[:6]]  # validation families
    by_family = {c.scenario_family_id: c for c in dataset.cases if c.case_id.endswith("_a")}

    items: list[dict[str, Any]] = []
    for i, fam in enumerate(families):
        case = by_family[fam]
        assert case.split in ("dev", "validation")
        trial = await execute_trial(dataset.load_case(case), config, ReferencePolicyClient(), prompt,
                                    repeat_index=1, seed=42)
        finals = [e for e in trial.events if e.event_type == "assistant_final"]
        response = finals[-1].payload["response"]
        context = build_judge_input(
            [e.payload["text"] for e in trial.events if e.event_type == "user_message"],
            observations(trial.events), response)
        split = "calibration" if i % 2 == 0 else "evaluation"
        kind, flawed = flaw(FLAWS[i % len(FLAWS)], response)
        for variant, resp, label, flaw_type in (("supported", response, "acceptable", None),
                                                ("flawed", flawed, "flawed", kind)):
            items.append({
                "response_id": f"{fam}__{variant}", "trace_id": f"{case.case_id}__r1",
                "family_id": fam, "judge_split": split, "construction": variant,
                "flaw_type": flaw_type, "proposed_label": label,
                "judge_input": {**context, "structured_answer": {k: v for k, v in resp.items() if k != "message"},
                                "message": resp["message"]},
            })

    OUT.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for split in ("calibration", "evaluation"):
        rows = [canonical_json(x) for x in items if x["judge_split"] == split]
        data = ("\n".join(rows) + "\n").encode()
        (OUT / f"{split}.jsonl").write_bytes(data)
        hashes[f"{split}.jsonl"] = sha256_bytes(data)
    review = OUT / "review.csv"
    existing = {}
    if review.exists():
        with review.open(newline="") as handle:
            existing = {r["response_id"]: r for r in csv.DictReader(handle)}
    with review.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS, lineterminator="\n")
        writer.writeheader()
        for x in items:
            prior = existing.get(x["response_id"], {})
            writer.writerow({"trace_id": x["trace_id"], "response_id": x["response_id"],
                             "family_id": x["family_id"], "proposed_label": x["proposed_label"],
                             "human_label": prior.get("human_label", ""), "reviewer_id": prior.get("reviewer_id", ""),
                             "reviewed_at": prior.get("reviewed_at", ""), "notes": prior.get("notes", "")})
    (OUT / "manifest.json").write_text(json.dumps({"name": "northstar-judge-v1", "files": hashes,
                                                   "families": len(families), "responses": len(items)},
                                                  indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(items)} responses from {len(families)} families to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
