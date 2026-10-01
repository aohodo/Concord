# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 4
- Passed: 3
- Failed: 1
- Pass rate: 75.0%
- Average wall time: 240.94s
- Recorded model decisions: 29
- Reached M2: 4/4
- Reached M3: 2/4
- M1 entered M2 on the first turn: 4/4
- Average user turns: 2.50
- Average user input characters: 140.5
- Average assistant response characters: 227.0
- Mean post-formulation time to first tool: 0.07s (4/4 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 2/2 | 104.43 | 9 |
| self_correction | 1/2 | 377.46 | 20 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| enterprise_it | 1/1 | {'human_required': 1} |
| platform_operations | 0/1 | {'evidence_required': 1} |
| saas_operations | 1/1 | {'resolved': 1} |
| software_delivery | 1/1 | {'resolved': 1} |

## Terminal status

- `evidence_required`: 1
- `human_required`: 1
- `resolved`: 2

## Failure labels

- `GOAL_NOT_VERIFIED`: 1
- `UNEXPECTED_FINAL_STATUS:evidence_required`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
