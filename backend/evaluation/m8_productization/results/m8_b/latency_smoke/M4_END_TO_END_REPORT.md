# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 3
- Passed: 3
- Failed: 0
- Pass rate: 100.0%
- Average wall time: 141.85s
- Recorded model decisions: 29
- Reached M2: 3/3
- Reached M3: 1/3
- M1 entered M2 on the first turn: 3/3
- Average user turns: 3.00
- Average user input characters: 119.0
- Average assistant response characters: 598.3
- Mean post-formulation time to first tool: 0.06s (3/3 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| fragmented_novice | 2/2 | 100.95 | 16 |
| self_correction | 1/1 | 223.65 | 13 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| enterprise_it | 1/1 | {'resolved': 1} |
| platform_operations | 2/2 | {'resolved': 2} |

## Terminal status

- `resolved`: 3

## Failure labels

- None

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
