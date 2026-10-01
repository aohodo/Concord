# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 1
- Passed: 0
- Failed: 1
- Pass rate: 0.0%
- Average wall time: 410.40s
- Recorded model decisions: 13
- Reached M2: 1/1
- Reached M3: 1/1
- M1 entered M2 on the first turn: 1/1
- Average user turns: 3.00
- Average user input characters: 171.0
- Average assistant response characters: 394.0
- Mean post-formulation time to first tool: 0.04s (1/1 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| self_correction | 0/1 | 410.40 | 13 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| platform_operations | 0/1 | {'waiting_for_user': 1} |

## Terminal status

- `waiting_for_user`: 1

## Failure labels

- `GOAL_NOT_VERIFIED`: 1
- `UNEXPECTED_FINAL_STATUS:waiting_for_user`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
