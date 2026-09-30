# M7 Real-Model Chain Check

Date: 2026-09-30

Model: `qwen3.8-flash`

Environment: resettable synthetic VPN state; model traffic launched with proxy variables cleared.

## Runs

1. Production-default permission boundary: the runtime ignored evaluation-only permissions, M2
   stopped at `collaboration_required`, and M7 returned `unverified`. No experience was retained.
2. Verified source Case: M2 used five tool calls, executed the simulated action, independently
   observed `vpn=connected` and `access=restored`, and M7 retained one active
   `successful_procedure` experience.
3. Verified reuse Case: M7 retrieved the prior experience with structured score `0.70`; the planner
   cited its ID but still executed five current-Case tool calls and independently reverified both
   success criteria. The source experience received `supported` feedback.

| run | phase | verdict | candidates | tool calls | latency |
|---|---|---|---:|---:|---:|
| permission boundary | collaboration | unverified | 0 | 2 observations | 66.125s |
| verified source | resolved | verified_success | 0 | 5 | 69.500s |
| verified reuse | resolved | verified_success | 1 | 5 | 58.953s |

## What this establishes

- Model self-assessment and an unresolved permission boundary did not enter active experience.
- A verified result did enter the repository and was available to another Case.
- Retrieval affected the planner's declared experience references without bypassing current-world
  execution or independent verification.
- Positive reuse updated the source experience's downstream feedback counters.

## Boundary

This is a three-run vertical chain check, not a statistically meaningful success-rate claim. The
64-scenario deterministic control matrix tests retrieval hazards; future longitudinal Episodes must
measure whether reuse lowers repeated failures, wall time or human effort across diverse problems.
