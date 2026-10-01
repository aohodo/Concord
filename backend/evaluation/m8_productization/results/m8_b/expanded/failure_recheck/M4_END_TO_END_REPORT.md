# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 5
- Passed: 4
- Failed: 1
- Pass rate: 80.0%
- Average wall time: 1478.01s
- Recorded model decisions: 40
- Reached M2: 4/5
- Reached M3: 2/5
- M1 entered M2 on the first turn: 4/5
- Average user turns: 2.60
- Average user input characters: 105.0
- Average assistant response characters: 213.0
- Mean post-formulation time to first tool: 0.07s (4/5 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 1/2 | 1801.47 | 6 |
| fragmented_novice | 2/2 | 1847.33 | 26 |
| self_correction | 1/1 | 92.42 | 8 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 1/1 | {'resolved': 1} |
| education_it | 0/1 | {'reconstructing_situation': 1} |
| enterprise_it | 1/1 | {'human_required': 1} |
| platform_operations | 1/1 | {'resolved': 1} |
| saas_operations | 1/1 | {'resolved': 1} |

## Terminal status

- `human_required`: 1
- `reconstructing_situation`: 1
- `resolved`: 3

## Failure labels

- `M2_NOT_REACHED`: 1
- `GOAL_NOT_VERIFIED`: 1
- `UNEXPECTED_FINAL_STATUS:reconstructing_situation`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
