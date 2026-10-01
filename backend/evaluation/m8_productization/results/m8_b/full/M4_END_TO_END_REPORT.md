# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 31
- Passed: 30
- Failed: 1
- Pass rate: 96.8%
- Average wall time: 179.17s
- Recorded model decisions: 214
- Reached M2: 31/31
- Reached M3: 4/31
- M1 entered M2 on the first turn: 30/31
- Average user turns: 2.68
- Average user input characters: 122.3
- Average assistant response characters: 234.8
- Mean post-formulation time to first tool: 2.17s (31/31 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 10/10 | 116.14 | 53 |
| fragmented_novice | 10/10 | 192.85 | 75 |
| self_correction | 10/11 | 224.05 | 86 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 3/3 | {'resolved': 3} |
| education_it | 3/3 | {'resolved': 3} |
| enterprise_it | 6/6 | {'resolved': 3, 'human_required': 3} |
| logistics | 3/3 | {'resolved': 3} |
| platform_operations | 6/7 | {'resolved': 6, 'no_benefit': 1} |
| saas_operations | 6/6 | {'resolved': 6} |
| software_delivery | 3/3 | {'resolved': 3} |

## Terminal status

- `human_required`: 3
- `no_benefit`: 1
- `resolved`: 27

## Failure labels

- `GOAL_NOT_VERIFIED`: 1
- `UNEXPECTED_FINAL_STATUS:no_benefit`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
