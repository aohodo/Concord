# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 31
- Passed: 21
- Failed: 10
- Pass rate: 67.7%
- Average wall time: 389.47s
- Recorded model decisions: 172
- Reached M2: 23/31
- Reached M3: 1/31
- M1 entered M2 on the first turn: 24/31
- Average user turns: 2.13
- Average user input characters: 99.5
- Average assistant response characters: 266.9
- Mean post-formulation time to first tool: 0.68s (23/31 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 7/10 | 313.00 | 40 |
| fragmented_novice | 7/10 | 452.43 | 69 |
| self_correction | 7/11 | 401.75 | 63 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 2/3 | {'resolved': 2, 'error': 1} |
| education_it | 2/3 | {'resolved': 2, 'reconstructing_situation': 1} |
| enterprise_it | 3/6 | {'resolved': 3, 'missing': 3} |
| logistics | 3/3 | {'resolved': 3} |
| platform_operations | 5/7 | {'resolved': 5, 'missing': 2} |
| saas_operations | 3/6 | {'resolved': 3, 'paused': 1, 'missing': 2} |
| software_delivery | 3/3 | {'resolved': 3} |

## Terminal status

- `error`: 1
- `missing`: 7
- `paused`: 1
- `reconstructing_situation`: 1
- `resolved`: 21

## Failure labels

- `M2_NOT_REACHED`: 8
- `EXCEPTION:ReadTimeout:`: 7
- `M1_BYPASSED_OR_UNOBSERVABLE`: 7
- `UNEXPECTED_FINAL_STATUS:missing`: 7
- `GOAL_NOT_VERIFIED`: 5
- `UNEXPECTED_FINAL_STATUS:reconstructing_situation`: 1
- `UNEXPECTED_FINAL_STATUS:error`: 1
- `UNEXPECTED_FINAL_STATUS:paused`: 1

## Non-failing coverage notes

- `M3_COVERAGE_NOT_REACHED`: 4

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
