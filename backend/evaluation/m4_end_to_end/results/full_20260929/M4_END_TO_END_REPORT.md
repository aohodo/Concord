# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 30
- Passed: 26
- Failed: 4
- Pass rate: 86.7%
- Average wall time: 154.40s
- Recorded model decisions: 195

## Terminal status

- `human_required`: 3
- `missing`: 1
- `resolved`: 26

## Failure labels

- `EXPECTED_M3_NOT_REACHED`: 3
- `EXCEPTION:ConnectError:All connection attempts failed`: 1
- `M1_BYPASSED_OR_UNOBSERVABLE`: 1
- `M2_NOT_REACHED`: 1
- `UNEXPECTED_FINAL_STATUS:missing`: 1

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
