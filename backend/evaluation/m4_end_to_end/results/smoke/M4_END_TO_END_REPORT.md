# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 1
- Passed: 0
- Failed: 1
- Pass rate: 0.0%
- Average wall time: 4.69s
- Recorded model decisions: 0

## Terminal status

- `missing`: 1

## Failure labels

- `EXCEPTION:JSONDecodeError:Expecting value: line 1 column 1 (char 0)`: 1
- `M1_BYPASSED_OR_UNOBSERVABLE`: 1
- `M2_NOT_REACHED`: 1
- `UNEXPECTED_FINAL_STATUS:missing`: 1

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
