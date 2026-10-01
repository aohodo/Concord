# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 111
- Passed: 111
- Failed: 0
- Pass rate: 100.0%
- Average wall time: 130.42s
- Recorded model decisions: 722
- Reached M2: 111/111
- Reached M3: 15/111
- M1 entered M2 on the first turn: 107/111
- Average user turns: 2.35
- Average user input characters: 137.7
- Average assistant response characters: 294.9
- Mean post-formulation time to first tool: 4.60s (111/111 measured)

## User-condition results

| condition | pass | average latency s | model decisions |
|---|---:|---:|---:|
| cross_domain_transfer | 10/10 | 141.15 | 53 |
| deadline_result_first | 10/10 | 103.57 | 54 |
| expert_precise | 10/10 | 150.85 | 61 |
| fragmented_novice | 10/10 | 153.01 | 76 |
| frustrated_repeat | 10/10 | 97.04 | 56 |
| half_expert_hypothesis | 10/10 | 99.09 | 58 |
| low_control | 10/10 | 177.02 | 85 |
| low_patience_stream | 10/10 | 117.05 | 72 |
| multi_issue_dump | 10/10 | 192.32 | 63 |
| procedural_only | 10/10 | 82.20 | 54 |
| self_correction | 11/11 | 122.12 | 90 |

## Domain results

| domain | pass | terminal statuses |
|---|---:|---|
| commerce | 11/11 | {'resolved': 11} |
| education_it | 11/11 | {'resolved': 11} |
| enterprise_it | 22/22 | {'resolved': 11, 'human_required': 11} |
| logistics | 11/11 | {'resolved': 11} |
| platform_operations | 23/23 | {'resolved': 23} |
| saas_operations | 22/22 | {'resolved': 22} |
| software_delivery | 11/11 | {'resolved': 11} |

## Terminal status

- `human_required`: 11
- `resolved`: 100

## Failure labels

- None

## Non-failing coverage notes

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
