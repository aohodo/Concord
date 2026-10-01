# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 42
- Passed: 41
- Failed: 1
- Pass rate: 97.6%
- Average wall time: 120.35s
- Recorded model decisions: 269
- Reached M2: 42/42
- Reached M3: 6/42
- M1 entered M2 on the first turn: 39/42
- Average user turns: 2.48
- Average user input characters: 120.0
- Average assistant response characters: 276.4
- Mean post-formulation time to first tool: 8.39s (42/42 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| cross_domain_transfer | 2/2 | 215.40 | 9 |
| deadline_result_first | 9/9 | 88.45 | 48 |
| expert_precise | 2/2 | 77.48 | 10 |
| fragmented_novice | 10/10 | 153.01 | 76 |
| frustrated_repeat | 2/2 | 127.27 | 11 |
| half_expert_hypothesis | 2/2 | 128.44 | 11 |
| low_control | 1/2 | 90.61 | 10 |
| low_patience_stream | 1/1 | 184.77 | 8 |
| multi_issue_dump | 1/1 | 66.76 | 4 |
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
| saas_operations | 11/12 | {'resolved': 11, 'waiting_for_user': 1} |
| software_delivery | 1/1 | {'resolved': 1} |

## Terminal status

- `human_required`: 3
- `resolved`: 38
- `waiting_for_user`: 1

## Failure labels

- `GOAL_NOT_VERIFIED`: 1
- `UNEXPECTED_FINAL_STATUS:waiting_for_user`: 1

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
