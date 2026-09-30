# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 1
- Passed: 0
- Failed: 1
- Pass rate: 0.0%
- Average wall time: 98.08s
- Recorded model decisions: 2
- Reached M2: 0/1
- Reached M3: 0/1
- M1 entered M2 on the first turn: 1/1
- Average user turns: 1.00
- Average user input characters: 21.0
- Average assistant response characters: 68.0
- Mean post-formulation time to first tool: unavailable

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| fragmented_novice | 0/1 | 98.08 | 2 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| enterprise_it | 0/1 | {'missing': 1} |

## Terminal status

- `missing`: 1

## Failure labels

- `EXCEPTION:ReadError:`: 1
- `M1_BYPASSED_OR_UNOBSERVABLE`: 1
- `M2_NOT_REACHED`: 1
- `GOAL_NOT_VERIFIED`: 1
- `UNEXPECTED_FINAL_STATUS:missing`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
