# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 31
- Passed: 31
- Failed: 0
- Pass rate: 100.0%
- Average wall time: 149.56s
- Recorded model decisions: 213
- Reached M2: 31/31
- Reached M3: 4/31
- M1 entered M2 on the first turn: 31/31
- Average user turns: 2.61
- Average user input characters: 119.9
- Average assistant response characters: 231.9
- Mean post-formulation time to first tool: 0.01s (31/31 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| deadline_result_first | 10/10 | 101.06 | 52 |
| fragmented_novice | 10/10 | 169.58 | 74 |
| self_correction | 11/11 | 175.45 | 87 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 3/3 | {'resolved': 3} |
| education_it | 3/3 | {'resolved': 3} |
| enterprise_it | 6/6 | {'resolved': 3, 'human_required': 3} |
| logistics | 3/3 | {'resolved': 3} |
| platform_operations | 7/7 | {'resolved': 7} |
| saas_operations | 6/6 | {'resolved': 6} |
| software_delivery | 3/3 | {'resolved': 3} |

## Terminal status

- `human_required`: 3
- `resolved`: 28

## Failure labels

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
