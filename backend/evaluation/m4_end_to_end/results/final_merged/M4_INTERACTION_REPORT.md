# M4 Interaction-quality Audit

This is an independent LLM-as-Judge view of visible interaction behavior. It is auxiliary evidence and cannot override environment-grounded outcomes.

- Episodes: 31
- Completed judgments: 31
- Judge errors: 0

## Dimensions

| dimension | PASS | PARTIAL | FAIL | NOT_APPLICABLE |
|---|---:|---:|---:|---:|
| cognitive_load | 10 | 15 | 6 | 0 |
| emotional_attunement | 7 | 15 | 9 | 0 |
| epistemic_humility | 17 | 13 | 1 | 0 |
| failure_memory | 13 | 7 | 0 | 11 |
| outcome_orientation | 10 | 17 | 4 | 0 |
| progress_truthfulness | 15 | 11 | 5 | 0 |

## Variant totals across dimensions

| variant | cases | PASS | PARTIAL | FAIL | NOT_APPLICABLE |
|---|---:|---:|---:|---:|---:|
| v2_full | 31 | 72 | 78 | 25 | 11 |

## Failure labels

- `USER_STATE_IGNORED`: 18
- `FALSE_PROGRESS`: 11
- `GOAL_DRIFT`: 7
- `HYPOTHESIS_AS_FACT`: 2
- `OVERLONG_RESPONSE`: 2
