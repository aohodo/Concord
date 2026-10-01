# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 36
- Passed: 34
- Failed: 2
- Pass rate: 94.4%
- Average wall time: 117.88s
- Recorded model decisions: 234
- Reached M2: 35/36
- Reached M3: 6/36
- M1 entered M2 on the first turn: 33/36
- Average user turns: 2.56
- Average user input characters: 116.0
- Average assistant response characters: 268.6
- Mean post-formulation time to first tool: 5.41s (35/36 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| cross_domain_transfer | 0/1 | 175.75 | 2 |
| deadline_result_first | 9/9 | 88.45 | 48 |
| expert_precise | 1/1 | 83.67 | 5 |
| fragmented_novice | 10/10 | 153.01 | 76 |
| frustrated_repeat | 1/1 | 67.01 | 5 |
| half_expert_hypothesis | 1/1 | 171.45 | 6 |
| low_control | 0/1 | 54.86 | 3 |
| low_patience_stream | 1/1 | 184.77 | 8 |
| multi_issue_dump | 1/1 | 66.76 | 4 |
| procedural_only | 1/1 | 53.06 | 4 |
| self_correction | 9/9 | 117.81 | 73 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 3/3 | {'resolved': 3} |
| education_it | 3/3 | {'resolved': 3} |
| enterprise_it | 12/14 | {'resolved': 9, 'formulation_error': 2, 'human_required': 3} |
| logistics | 3/3 | {'resolved': 3} |
| platform_operations | 6/6 | {'resolved': 6} |
| saas_operations | 6/6 | {'resolved': 6} |
| software_delivery | 1/1 | {'resolved': 1} |

## Terminal status

- `formulation_error`: 2
- `human_required`: 3
- `resolved`: 31

## Failure labels

- `GOAL_NOT_VERIFIED`: 2
- `UNEXPECTED_FINAL_STATUS:formulation_error`: 2
- `M2_NOT_REACHED`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
