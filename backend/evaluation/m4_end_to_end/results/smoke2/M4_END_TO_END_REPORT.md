# Concord M4 End-to-End Evaluation Report

## Scope

Every recorded Episode entered through HTTP `/chat` from raw multi-turn user input. The evaluator did not construct M1 ResolutionContext or M2 CollaborationHandoff.

## Summary

- Episodes: 1
- Passed: 1
- Failed: 0
- Pass rate: 100.0%
- Average wall time: 147.32s
- Recorded model decisions: 7

## Terminal status

- `resolved`: 1

## Failure labels

- None

## Interpretation boundary

A PASS proves the declared simulated outcome and structural chain checks for that Episode. It does not prove universal domain competence. LLM-as-Judge results, when added, must remain separate from environment-grounded success.
