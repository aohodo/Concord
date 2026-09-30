# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 4
- Passed: 4
- Failed: 0
- Pass rate: 100.0%
- Average wall time: 139.51s
- Recorded model decisions: 26
- Reached M2: 4/4
- Reached M3: 1/4
- M1 entered M2 on the first turn: 4/4
- Average user turns: 2.75
- Average user input characters: 118.5
- Average assistant response characters: 235.5
- Mean post-formulation time to first tool: 30.71s (4/4 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 1/1 | 125.01 | 6 |
| fragmented_novice | 2/2 | 139.54 | 13 |
| self_correction | 1/1 | 153.93 | 7 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| education_it | 1/1 | {'resolved': 1} |
| enterprise_it | 1/1 | {'human_required': 1} |
| logistics | 1/1 | {'resolved': 1} |
| saas_operations | 1/1 | {'resolved': 1} |

## Terminal status

- `human_required`: 1
- `resolved`: 3

## Failure labels

- None

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
