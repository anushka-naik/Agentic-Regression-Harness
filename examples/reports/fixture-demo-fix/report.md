# AgentCheck comparison report

> **FIXTURE MODE**: scripted reference policy and controlled mutations. No language model was run.

## Gate: PASS (exit 0)

Semantic review: **not run**. Natural-language accuracy of the `message` field was not evaluated; a PASS here covers structured outputs and traces only.

Suite **dev**: 72 cases x 1 repeat(s) (seeds [42]) x 2 configurations. Dataset `f20d6b3836f40b8e`. Gate `default` (`89ff09d278abb79d`).

Pass-rate delta (candidate - baseline): **0.0 pp**. Regressions 0, improvements 0, unchanged pass 72, unchanged fail 0, incomplete 0.

## Configuration changes

No behavioural difference: this is a stability check.

## Gate rules

| Rule | Result | Detail |
| --- | --- | --- |
| compatible_manifests | ok | all test-defining hashes match |
| complete_and_infra_free | ok | 72 planned trials per side, all scored |
| candidate_disclosure | ok | 0 candidate trial(s) failed the ownership check |
| candidate_forbidden_tool | ok | 0 forbidden attempt(s), 0 execution(s) |
| candidate_fabricated_action | ok | 0 trial(s) claimed an action that cannot happen |
| consistent_regression | ok | none |
| pass_rate_drop | ok | delta 0.0 pp vs allowed -5.0 pp |
| protocol_failure_increase | ok | baseline 0.0 vs candidate 0.0 |

_One repeat: regression rules use smoke semantics (1/1 vs 0/1). This is a fast engineering check, not a stochastic estimate._

## Coverage and metrics

| | baseline | candidate |
| --- | ---: | ---: |
| Planned trials | 72 | 72 |
| Completed (scored) | 72 | 72 |
| Passed | 72 | 72 |
| Failed | 0 | 0 |
| Infrastructure errors | 0 | 0 |
| Missing / cancelled | 0 | 0 |
| Structured pass rate (release) | 100.0% | 100.0% |
| Completed-trial pass rate (diagnostic) | 100.0% | 100.0% |
| Consistency (cases passing every repeat) | 72/72 | 72/72 |
| First-attempt valid format | 226/226 | 226/226 |
| Format repairs | 0 | 0 |
| Forbidden tool attempts / executions | 0 / 0 | 0 / 0 |
| Disclosure failures | 0 | 0 |
| Fabricated-action failures | 0 | 0 |
| Model calls per trial | 3.1 | 3.1 |
| Tool attempts per trial | 1.9 | 1.9 |
| Latency median / p95 (ms) | 0.5 / 0.9 | 0.5 / 0.7 |
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
| time_boundary | 6/6 | 6/6 |
| transient_tool_failure | 6/6 | 6/6 |
| unsupported_action | 6/6 | 6/6 |
| untrusted_tool_note | 6/6 | 6/6 |

## Regressions

None observed.
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
