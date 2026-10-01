# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 42
- Passed: 34
- Failed: 8
- Pass rate: 81.0%
- Average wall time: 105.98s
- Recorded model decisions: 259
- Reached M2: 42/42
- Reached M3: 14/42
- M1 entered M2 on the first turn: 40/42
- Average user turns: 2.48
- Average user input characters: 120.0
- Average assistant response characters: 251.6
- Mean post-formulation time to first tool: 4.52s (42/42 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| cross_domain_transfer | 0/2 | 59.09 | 7 |
| deadline_result_first | 9/9 | 88.45 | 48 |
| expert_precise | 1/2 | 65.69 | 9 |
| fragmented_novice | 10/10 | 153.01 | 76 |
| frustrated_repeat | 1/2 | 58.79 | 9 |
| half_expert_hypothesis | 1/2 | 117.62 | 10 |
| low_control | 0/2 | 54.73 | 8 |
| low_patience_stream | 1/1 | 184.77 | 8 |
| multi_issue_dump | 1/1 | 66.76 | 4 |
| procedural_only | 1/2 | 50.62 | 7 |
| self_correction | 9/9 | 117.81 | 73 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 3/3 | {'resolved': 3} |
| education_it | 3/3 | {'resolved': 3} |
| enterprise_it | 12/14 | {'resolved': 9, 'human_required': 5} |
| logistics | 3/3 | {'resolved': 3} |
| platform_operations | 6/6 | {'resolved': 6} |
| saas_operations | 6/12 | {'resolved': 6, 'human_required': 6} |
| software_delivery | 1/1 | {'resolved': 1} |

## Terminal status

- `human_required`: 11
- `resolved`: 31

## Failure labels

- `GOAL_NOT_VERIFIED`: 8
- `UNEXPECTED_FINAL_STATUS:human_required`: 8

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
