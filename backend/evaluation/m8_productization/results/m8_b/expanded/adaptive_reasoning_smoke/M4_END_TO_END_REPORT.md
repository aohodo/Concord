# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 1
- Passed: 1
- Failed: 0
- Pass rate: 100.0%
- Average wall time: 79.60s
- Recorded model decisions: 5
- Reached M2: 1/1
- Reached M3: 0/1
- M1 entered M2 on the first turn: 1/1
- Average user turns: 2.00
- Average user input characters: 79.0
- Average assistant response characters: 40.0
- Mean post-formulation time to first tool: 0.07s (1/1 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 1/1 | 79.60 | 5 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| education_it | 1/1 | {'resolved': 1} |

## Terminal status

- `resolved`: 1

## Failure labels

- None

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
