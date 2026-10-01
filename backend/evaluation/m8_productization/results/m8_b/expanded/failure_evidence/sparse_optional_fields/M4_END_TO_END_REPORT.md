# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 44
- Passed: 43
- Failed: 1
- Pass rate: 97.7%
- Average wall time: 126.69s
- Recorded model decisions: 280
- Reached M2: 43/44
- Reached M3: 6/44
- M1 entered M2 on the first turn: 39/44
- Average user turns: 2.52
- Average user input characters: 124.3
- Average assistant response characters: 280.1
- Mean post-formulation time to first tool: 11.68s (43/44 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| cross_domain_transfer | 2/2 | 215.40 | 9 |
| deadline_result_first | 9/9 | 88.45 | 48 |
| expert_precise | 2/2 | 77.48 | 10 |
| fragmented_novice | 10/10 | 153.01 | 76 |
| frustrated_repeat | 2/2 | 127.27 | 11 |
| half_expert_hypothesis | 2/2 | 128.44 | 11 |
| low_control | 2/2 | 89.73 | 13 |
| low_patience_stream | 2/2 | 198.09 | 14 |
| multi_issue_dump | 1/2 | 188.47 | 6 |
| procedural_only | 2/2 | 69.14 | 9 |
| self_correction | 9/9 | 117.81 | 73 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 3/3 | {'resolved': 3} |
| education_it | 3/3 | {'resolved': 3} |
| enterprise_it | 14/14 | {'resolved': 11, 'human_required': 3} |
| logistics | 3/3 | {'resolved': 3} |
| platform_operations | 6/6 | {'resolved': 6} |
| saas_operations | 13/14 | {'resolved': 13, 'reconstructing_situation': 1} |
| software_delivery | 1/1 | {'resolved': 1} |

## Terminal status

- `human_required`: 3
- `reconstructing_situation`: 1
- `resolved`: 40

## Failure labels

- `M2_NOT_REACHED`: 1
- `GOAL_NOT_VERIFIED`: 1
- `UNEXPECTED_FINAL_STATUS:reconstructing_situation`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
