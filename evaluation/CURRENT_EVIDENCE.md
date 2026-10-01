# Concord Current Evidence

Updated: 2026-09-30. This is the current evidence index. Historical milestone reports are retained
and are not rewritten to imply stronger results.

## Verified on the M8-B source tree

- Backend regression: `202 passed`.
- Frontend production build: Vite 8.3.1 (final M8-B validation report is generated separately).
- Real `qwen3.8-flash` longitudinal catalog: 31 Episodes, 7 domains, 3 user conditions.
  - first pass: 30/31;
  - one structural failure was preserved, diagnosed and rerun after the fix;
  - catalog-complete merged evidence: 31/31;
  - 28 `resolved`, 3 correct `human_required` boundaries;
  - 4 fault-injection Episodes all reached their declared outcomes;
  - mean / median / p95 latency: 180.48 / 183.10 / 297.11 seconds;
  - 221 model decisions and 162 audited tool calls.
- The recorded end-to-end experiment covers M1 → M2 → conditional M3 → M2 consumption → independent
  verification → verified-outcome observation. It took 381.46 seconds, 3 user turns, 14 model
  calls and 7 tool calls.
- Four-Episode mechanism control:
  - full, M2-only, fixed topology, no-user-state and no-epistemic-separation each passed 4/4;
  - M2-only used 22 model calls versus the full runtime's 32, so this subset does not support forcing M3;
  - no-failure-memory passed 3/4 and stopped the targeted retry Case at `evidence_required`;
  - this one clean difference is a causal candidate, not a general success-rate claim;
  - an earlier 8,080-second row crossed host sleep and is excluded as infrastructure-invalid.
- M7.5 deterministic memory controls: 104 scenarios × 3 variants = 312 runs.
  - no memory: 72/104 correct, 0/32 useful memories retrieved;
  - coarse structured baseline: 64/104 correct, 32/32 useful retrieved, 40/72 unsafe retrieved;
  - structured verified: 104/104 correct, 32/32 useful retrieved, 72/72 hazards rejected.
- M7.5 real `qwen3.8-flash` source→target chains: 3 domains, 3/3 source outcomes and 3/3 target
  outcomes independently verified; every target completed retrieved → considered → adopted →
  verified attribution and re-executed five tool calls.

## What these results support

- Raw multi-turn input reaches the real HTTP product path; the evaluator does not bypass M1 or
  construct an M2/M3 handoff.
- M2 does not resolve a Case merely because any observation exists: declared success criteria must
  bind to runtime-valid evidence.
- Retryable failures, lost responses after commit, stale work, permissions and user corrections are
  represented as longitudinal state transitions rather than answer-only prompts.
- Dynamic M3 is conditional, source-attributed and advisory. M2 remains Case Owner and must verify
  any adopted recommendation with the environment.
- Durable experience excludes raw tool arguments/results, persists only allowlisted fields and
  requires current-Case re-verification before receiving outcome credit.

## What these results do not support

- The final 31/31 is a provenance-preserving merge, not a fresh independent full-catalog repetition.
- The simulated domains do not establish production competence in real organizations.
- Mean latency is too high for a polished interactive product; the longest recorded Case took
  381.46 seconds.
- Existing experience experiments prove safe wiring and attribution, not a general success-rate or
  latency improvement.
- The mechanism comparison is deliberately small; it is not a statistically powered causal
  study or a user study.
- The results do not establish production security, broad multimodal quality or real-system write
  integration.

## Reproduction

```powershell
# From the repository root
.\scripts\verify.ps1
.\scripts\demo.ps1

# With an evaluation-enabled backend already running:
.\scripts\demo.ps1 -Live
```

Current artifacts:

- `backend/evaluation/m8_productization/results/m8_b/full/`
- `backend/evaluation/m8_productization/results/m8_b/reruns/`
- `backend/evaluation/m8_productization/results/m8_b/final/`
- `backend/evaluation/m8_productization/results/m8_b/comparison/`
- `backend/evaluation/m7_continual_improvement/results/latest/`
- `backend/evaluation/m7_continual_improvement/results/m7_5_real/`
