# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 55
- Passed: 54
- Failed: 1
- Pass rate: 98.2%
- Average wall time: 122.93s
- Recorded model decisions: 345
- Reached M2: 54/55
- Reached M3: 6/55
- M1 entered M2 on the first turn: 50/55
- Average user turns: 2.45
- Average user input characters: 130.0
- Average assistant response characters: 292.9
- Mean post-formulation time to first tool: 9.32s (54/55 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| cross_domain_transfer | 3/3 | 172.05 | 14 |
| deadline_result_first | 9/9 | 88.45 | 48 |
| expert_precise | 3/4 | 181.25 | 19 |
| fragmented_novice | 10/10 | 153.01 | 76 |
| frustrated_repeat | 3/3 | 107.68 | 16 |
| half_expert_hypothesis | 4/4 | 104.58 | 23 |
| low_control | 3/3 | 91.38 | 20 |
| low_patience_stream | 3/3 | 166.49 | 21 |
| multi_issue_dump | 3/3 | 110.26 | 15 |
| procedural_only | 4/4 | 71.99 | 20 |
| self_correction | 9/9 | 117.81 | 73 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 3/3 | {'resolved': 3} |
| education_it | 11/11 | {'resolved': 11} |
| enterprise_it | 14/14 | {'resolved': 11, 'human_required': 3} |
| logistics | 5/6 | {'resolved': 5, 'reconstructing_situation': 1} |
| platform_operations | 6/6 | {'resolved': 6} |
| saas_operations | 14/14 | {'resolved': 14} |
| software_delivery | 1/1 | {'resolved': 1} |

## Terminal status

- `human_required`: 3
- `reconstructing_situation`: 1
- `resolved`: 51

## Failure labels

- `M2_NOT_REACHED`: 1
- `GOAL_NOT_VERIFIED`: 1
- `UNEXPECTED_FINAL_STATUS:reconstructing_situation`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
