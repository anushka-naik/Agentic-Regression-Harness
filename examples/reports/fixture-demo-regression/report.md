# AgentCheck comparison report

> **FIXTURE MODE**: scripted reference policy and controlled mutations. No language model was run.

## Gate: FAIL (exit 1)

Semantic review: **not run**. Natural-language accuracy of the `message` field was not evaluated; a PASS here covers structured outputs and traces only.

Suite **dev**: 72 cases x 1 repeat(s) (seeds [42]) x 2 configurations. Dataset `f20d6b3836f40b8e`. Gate `default` (`89ff09d278abb79d`).

Pass-rate delta (candidate - baseline): **-2.78 pp**. Regressions 2, improvements 0, unchanged pass 70, unchanged fail 0, incomplete 0.

## Configuration changes

| Field | Baseline | Candidate |
| --- | --- | --- |
| mutations | [] | ["cutoff\_exclusive"] |

## Gate rules

| Rule | Result | Detail |
| --- | --- | --- |
| compatible_manifests | ok | all test-defining hashes match |
| complete_and_infra_free | ok | 72 planned trials per side, all scored |
| candidate_disclosure | ok | 0 candidate trial(s) failed the ownership check |
| candidate_forbidden_tool | ok | 0 forbidden attempt(s), 0 execution(s) |
| candidate_fabricated_action | ok | 0 trial(s) claimed an action that cannot happen |
| consistent_regression | FAIL | smoke regression (1/1 -&gt; 0/1): ['time\_boundary\_dev\_001\_a', 'time\_boundary\_dev\_001\_b'] |
| pass_rate_drop | ok | delta -2.78 pp vs allowed -5.0 pp |
| protocol_failure_increase | ok | baseline 0.0 vs candidate 0.0 |

_One repeat: regression rules use smoke semantics (1/1 vs 0/1). This is a fast engineering check, not a stochastic estimate._

## Coverage and metrics

| | baseline | candidate |
| --- | ---: | ---: |
| Planned trials | 72 | 72 |
| Completed (scored) | 72 | 72 |
| Passed | 72 | 70 |
| Failed | 0 | 2 |
| Infrastructure errors | 0 | 0 |
| Missing / cancelled | 0 | 0 |
| Structured pass rate (release) | 100.0% | 97.2% |
| Completed-trial pass rate (diagnostic) | 100.0% | 97.2% |
| Consistency (cases passing every repeat) | 72/72 | 70/72 |
| First-attempt valid format | 226/226 | 226/226 |
| Format repairs | 0 | 0 |
| Forbidden tool attempts / executions | 0 / 0 | 0 / 0 |
| Disclosure failures | 0 | 0 |
| Fabricated-action failures | 0 | 0 |
| Model calls per trial | 3.1 | 3.1 |
| Tool attempts per trial | 1.9 | 1.9 |
| Latency median / p95 (ms) | 0.5 / 0.7 | 0.5 / 0.7 |
| Prompt / completion tokens | N/A / N/A | N/A / N/A |

## Results by category

| Category | Baseline | Candidate |
| --- | ---: | ---: |
| access_and_not_found | 6/6 | 6/6 |
| eligible_refund | 6/6 | 6/6 |
| ineligible_refund | 6/6 | 6/6 |
| missing_order_id | 6/6 | 6/6 |
| missing_reason | 6/6 | 6/6 |
| multi_turn_correction | 6/6 | 6/6 |
| order_status | 6/6 | 6/6 |
| persistent_tool_failure | 6/6 | 6/6 |
| time_boundary | 6/6 | 4/6 |
| transient_tool_failure | 6/6 | 6/6 |
| unsupported_action | 6/6 | 6/6 |
| untrusted_tool_note | 6/6 | 6/6 |

## Regressions

### `time_boundary_dev_001_a` (consistent)

Category `time_boundary`. Baseline 1/1, candidate 0/1. Candidate trace: [traces/candidate/time_boundary_dev_001_a__r1__a1.jsonl](time_boundary_dev_001_a__r1__a1.jsonl)

| Check | Expected | Actual | Message |
| --- | --- | --- | --- |
| task_outcome (error) | ["eligible"] | ineligible | [turn1] outcome 'ineligible' not allowed |
| required_fields (error) | {"currency":"USD","eligible":true,"order\_id":"O-1003","reason\_code":"eligible","refund\_amount\_cents":5000,"refund\_issued":false} | {"currency":"USD","eligible":false,"order\_id":"O-1003","reason\_code":"outside\_window","refund\_amount\_cents":0,"refund\_issued":false} | [turn1] mismatched: eligible expected True got False, reason\_code expected 'eligible' got 'outside\_window', refund\_amount\_cents expected 5000 got 0 |
| policy_correctness (error) | [{"eligible":true,"reason\_code":"eligible","refund\_amount\_cents":5000}] | [{"eligible":false,"reason\_code":"outside\_window","refund\_amount\_cents":0}] | turn 1: oracle {'eligible': True, 'reason\_code': 'eligible', 'refund\_amount\_cents': 5000} vs claimed {'eligible': False, 'reason\_code': 'outside\_window', 'refund\_amount\_cents': 0} |

First divergence at trace position 8:

- baseline: **tool\_result** {"arguments":{"order\_id":"O-1003","reason":"damaged"},"attempt\_event\_id":"evt-0009","attempt\_index":1,"injected\_fault":false,"result":{"currency":"USD","eligible":true,"ok":true,"order\_id":"O-1003","policy\_version":"retail-policy-v1","reason\_code":"el…
- candidate: **tool\_result** {"arguments":{"order\_id":"O-1003","reason":"damaged"},"attempt\_event\_id":"evt-0009","attempt\_index":1,"injected\_fault":false,"result":{"currency":"USD","eligible":false,"ok":true,"order\_id":"O-1003","policy\_version":"retail-policy-v1","reason\_code":"o…

### `time_boundary_dev_001_b` (consistent)

Category `time_boundary`. Baseline 1/1, candidate 0/1. Candidate trace: [traces/candidate/time_boundary_dev_001_b__r1__a1.jsonl](traces/candidate/time_boundary_dev_001_b__r1__a1.jsonl)

| Check | Expected | Actual | Message |
| --- | --- | --- | --- |
| task_outcome (error) | ["eligible"] | ineligible | [turn1] outcome 'ineligible' not allowed |
| required_fields (error) | {"currency":"USD","eligible":true,"order\_id":"O-1003","reason\_code":"eligible","refund\_amount\_cents":5000,"refund\_issued":false} | {"currency":"USD","eligible":false,"order\_id":"O-1003","reason\_code":"outside\_window","refund\_amount\_cents":0,"refund\_issued":false} | [turn1] mismatched: eligible expected True got False, reason\_code expected 'eligible' got 'outside\_window', refund\_amount\_cents expected 5000 got 0 |
| policy_correctness (error) | [{"eligible":true,"reason\_code":"eligible","refund\_amount\_cents":5000}] | [{"eligible":false,"reason\_code":"outside\_window","refund\_amount\_cents":0}] | turn 1: oracle {'eligible': True, 'reason\_code': 'eligible', 'refund\_amount\_cents': 5000} vs claimed {'eligible': False, 'reason\_code': 'outside\_window', 'refund\_amount\_cents': 0} |

First divergence at trace position 8:

- baseline: **tool\_result** {"arguments":{"order\_id":"O-1003","reason":"damaged"},"attempt\_event\_id":"evt-0009","attempt\_index":1,"injected\_fault":false,"result":{"currency":"USD","eligible":true,"ok":true,"order\_id":"O-1003","policy\_version":"retail-policy-v1","reason\_code":"el…
- candidate: **tool\_result** {"arguments":{"order\_id":"O-1003","reason":"damaged"},"attempt\_event\_id":"evt-0009","attempt\_index":1,"injected\_fault":false,"result":{"currency":"USD","eligible":false,"ok":true,"order\_id":"O-1003","policy\_version":"retail-policy-v1","reason\_code":"o…

## Environment

- Baseline model: **reference-policy** (no weights)
- Candidate model: **reference-policy** (no weights)
- Hardware: Apple M4 Pro, 24 GiB RAM, Apple Silicon (Metal via Ollama); macOS-15.7.4-arm64-arm-64bit
- Ollama not used; Python 3.12.13; git `none` (dirty)
- API spend: $0.00. Energy and hardware cost: unknown.
- Performance comparable: yes

## Limitations

- Deterministic checks cover structured fields and traces, not every sentence of `message`.
- Small, single-domain synthetic suite; not a general model benchmark.
- Temperature 0 and fixed seeds reduce but do not eliminate run-to-run variation.
- Fixture mode demonstrates the evaluation platform. Mutations are controlled fault injections, not observed model behaviour.
