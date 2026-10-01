# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 31
- Passed: 29
- Failed: 2
- Pass rate: 93.5%
- Average wall time: 724.83s
- Recorded model decisions: 204
- Reached M2: 29/31
- Reached M3: 7/31
- M1 entered M2 on the first turn: 27/31
- Average user turns: 2.52
- Average user input characters: 114.2
- Average assistant response characters: 274.7
- Mean post-formulation time to first tool: 6.51s (29/31 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 9/10 | 707.86 | 48 |
| fragmented_novice | 10/10 | 153.01 | 76 |
| self_correction | 10/11 | 1260.10 | 80 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 3/3 | {'resolved': 3} |
| education_it | 3/3 | {'resolved': 3} |
| enterprise_it | 6/6 | {'resolved': 3, 'human_required': 3} |
| logistics | 3/3 | {'resolved': 3} |
| platform_operations | 6/7 | {'resolved': 6, 'missing': 1} |
| saas_operations | 6/6 | {'resolved': 6} |
| software_delivery | 2/3 | {'resolved': 2, 'missing': 1} |

## Terminal status

- `human_required`: 3
- `missing`: 2
- `resolved`: 26

## Failure labels

- `EXCEPTION:ReadTimeout:`: 2
- `M1_BYPASSED_OR_UNOBSERVABLE`: 2
- `M2_NOT_REACHED`: 2
- `GOAL_NOT_VERIFIED`: 2
- `UNEXPECTED_FINAL_STATUS:missing`: 2

## Non-failing coverage notes

- `M3_COVERAGE_NOT_REACHED`: 1

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
