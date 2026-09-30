# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 4
- Passed: 1
- Failed: 3
- Pass rate: 25.0%
- Average wall time: 134.72s
- Recorded model decisions: 22
- Reached M2: 4/4
- Reached M3: 4/4
- M1 entered M2 on the first turn: 4/4
- Average user turns: 2.75
- Average user input characters: 118.5
- Average assistant response characters: 177.2
- Mean post-formulation time to first tool: 33.86s (4/4 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 0/1 | 82.11 | 3 |
| fragmented_novice | 1/2 | 143.73 | 12 |
| self_correction | 0/1 | 169.31 | 7 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| education_it | 0/1 | {'human_required': 1} |
| enterprise_it | 1/1 | {'human_required': 1} |
| logistics | 0/1 | {'human_required': 1} |
| saas_operations | 0/1 | {'human_required': 1} |

## Terminal status

- `human_required`: 4

## Failure labels

- `GOAL_NOT_VERIFIED`: 3
- `UNEXPECTED_FINAL_STATUS:human_required`: 3

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
