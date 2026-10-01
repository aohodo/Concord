# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 82
- Passed: 81
- Failed: 1
- Pass rate: 98.8%
- Average wall time: 129.89s
- Recorded model decisions: 534
- Reached M2: 82/82
- Reached M3: 7/82
- M1 entered M2 on the first turn: 78/82
- Average user turns: 2.39
- Average user input characters: 137.9
- Average assistant response characters: 290.8
- Mean post-formulation time to first tool: 6.18s (82/82 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| cross_domain_transfer | 7/7 | 169.41 | 38 |
| deadline_result_first | 9/9 | 88.45 | 48 |
| expert_precise | 7/7 | 147.68 | 39 |
| fragmented_novice | 10/10 | 153.01 | 76 |
| frustrated_repeat | 6/7 | 112.95 | 44 |
| half_expert_hypothesis | 7/7 | 93.97 | 39 |
| low_control | 7/7 | 163.57 | 62 |
| low_patience_stream | 6/6 | 130.43 | 42 |
| multi_issue_dump | 6/6 | 173.35 | 34 |
| procedural_only | 7/7 | 89.80 | 39 |
| self_correction | 9/9 | 117.81 | 73 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 11/11 | {'resolved': 11} |
| education_it | 11/11 | {'resolved': 11} |
| enterprise_it | 14/14 | {'resolved': 11, 'human_required': 3} |
| logistics | 11/11 | {'resolved': 11} |
| platform_operations | 19/20 | {'resolved': 19, 'evidence_required': 1} |
| saas_operations | 14/14 | {'resolved': 14} |
| software_delivery | 1/1 | {'resolved': 1} |

## Terminal status

- `evidence_required`: 1
- `human_required`: 3
- `resolved`: 78

## Failure labels

- `GOAL_NOT_VERIFIED`: 1
- `UNEXPECTED_FINAL_STATUS:evidence_required`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
