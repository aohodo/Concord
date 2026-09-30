# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 1
- Passed: 1
- Failed: 0
- Pass rate: 100.0%
- Average wall time: 218.69s
- Recorded model decisions: 8
- Reached M2: 1/1
- Reached M3: 0/1
- M1 entered M2 on the first turn: 1/1
- Average user turns: 3.00
- Average user input characters: 91.0
- Average assistant response characters: 418.0
- Mean post-formulation time to first tool: 0.01s (1/1 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| fragmented_novice | 1/1 | 218.69 | 8 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| enterprise_it | 1/1 | {'resolved': 1} |

## Terminal status

- `resolved`: 1

## Failure labels

- None

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
