# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 2
- Passed: 1
- Failed: 1
- Pass rate: 50.0%
- Average wall time: 199.89s
- Recorded model decisions: 19
- Reached M2: 2/2
- Reached M3: 1/2
- M1 entered M2 on the first turn: 2/2
- Average user turns: 2.50
- Average user input characters: 153.0
- Average assistant response characters: 238.5
- Mean post-formulation time to first tool: 0.02s (2/2 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 0/1 | 208.22 | 11 |
| self_correction | 1/1 | 191.57 | 8 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| logistics | 1/1 | {'resolved': 1} |
| saas_operations | 0/1 | {'evidence_required': 1} |

## Terminal status

- `evidence_required`: 1
- `resolved`: 1

## Failure labels

- `UNEXPECTED_FINAL_STATUS:evidence_required`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
