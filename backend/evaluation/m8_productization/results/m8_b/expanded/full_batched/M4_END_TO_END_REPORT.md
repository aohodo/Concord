# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 8
- Passed: 6
- Failed: 2
- Pass rate: 75.0%
- Average wall time: 128.91s
- Recorded model decisions: 48
- Reached M2: 7/8
- Reached M3: 0/8
- M1 entered M2 on the first turn: 7/8
- Average user turns: 2.62
- Average user input characters: 109.6
- Average assistant response characters: 304.4
- Mean post-formulation time to first tool: 0.08s (7/8 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 2/3 | 112.99 | 15 |
| fragmented_novice | 2/3 | 165.38 | 21 |
| self_correction | 2/2 | 98.07 | 12 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| education_it | 1/2 | {'resolved': 1, 'formulation_error': 1} |
| enterprise_it | 2/3 | {'error': 1, 'resolved': 2} |
| saas_operations | 3/3 | {'resolved': 3} |

## Terminal status

- `error`: 1
- `formulation_error`: 1
- `resolved`: 6

## Failure labels

- `UNEXPECTED_FINAL_STATUS:error`: 1
- `M2_NOT_REACHED`: 1
- `GOAL_NOT_VERIFIED`: 1
- `UNEXPECTED_FINAL_STATUS:formulation_error`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
