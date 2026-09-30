# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 2
- Passed: 2
- Failed: 0
- Pass rate: 100.0%
- Average wall time: 117.09s
- Recorded model decisions: 13
- Reached M2: 2/2
- Reached M3: 0/2
- M1 entered M2 on the first turn: 2/2
- Average user turns: 2.50
- Average user input characters: 153.0
- Average assistant response characters: 188.0
- Mean post-formulation time to first tool: 0.03s (2/2 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 1/1 | 95.98 | 6 |
| self_correction | 1/1 | 138.20 | 7 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| logistics | 1/1 | {'resolved': 1} |
| saas_operations | 1/1 | {'resolved': 1} |

## Terminal status

- `resolved`: 2

## Failure labels

- None

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
