# Data card: northstar-retail-v1

**Generated file. Edit `scripts/generate_dataset.py`, not this file.**

| | |
| --- | --- |
| Content | Original synthetic support scenarios for the fictional retailer Northstar Demo Store |
| Real customer data | None. Every identifier, product, amount and note is synthetic |
| Policy | Fictional refund policy `retail-policy-v1` (see `samples/retail/policy.md`); not legal advice |
| Generator | `scripts/generate_dataset.py --seed 42` (byte-identical output for identical inputs) |
| Content hash | `f20d6b3836f40b8e86a20bd41bee6d2366993a2c12c4a8c175fa3fd8d8019b1e` |
| License | CC0-1.0 (see `DATA_LICENSE` at the repository root) |

## Construction

60 scenario families were written by hand: 12 categories x 5 families, each with two
wording variants (120 cases). Families differ in state or interaction (order status,
timing, ownership, faults, dialogue shape), not only in names. A seeded RNG chooses order
IDs, customers, amounts, products and delivery offsets for generated worlds. The eight
seed rows (O-1001..O-1008) are fixed. Expected outcomes are written directly from the
scenario design and cross-checked against the independent reference oracle; any
contradiction aborts generation.

## Counts

| Category | Cases |
| --- | ---: |
| order_status | 10 |
| eligible_refund | 10 |
| ineligible_refund | 10 |
| time_boundary | 10 |
| missing_order_id | 10 |
| missing_reason | 10 |
| access_and_not_found | 10 |
| transient_tool_failure | 10 |
| persistent_tool_failure | 10 |
| untrusted_tool_note | 10 |
| unsupported_action | 10 |
| multi_turn_correction | 10 |
| **Total** | **120** |

Splits are assigned per family, never per paraphrase: three families per category in
`dev` (72 cases), one in `validation`
(24), one in `heldout` (24).
Families that share a `semantic_parent_id` (for example every scenario built on the seed
request "O-1001 arrived damaged") are kept in the same split; the validator enforces it.
The smoke suite is the first `dev` family's variant `a` in each category (12 cases).

## Review status

Every case is `machine_checked`: schema-valid and consistent with the reference oracle.
**No case has been human reviewed.** Before presenting the suite as reviewed, a person
should review the policy table, the boundary cases (`time_boundary_*`), and a stratified
sample, then record reviewer and date in `review.csv`. Coding agents must not fill in
reviewer fields.

## Known limitations

* Small and single-domain: a regression suite for one fictional policy, not a benchmark.
* Language is templated; real customers phrase things more variably.
* The held-out split is public. It guards against tuning on it during development; it
  says nothing about whether a pretrained model has seen similar text.
* Natural-language accuracy of the `message` field is outside the deterministic labels.
