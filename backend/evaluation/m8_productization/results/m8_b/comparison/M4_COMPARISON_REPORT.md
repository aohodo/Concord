# M4 Mechanism Comparison

| variant | pass | resolved | M3 reached | model calls | tool calls | avg turns | avg response chars | avg latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| v2_full | 4/4 | 3 | 2 | 32 | 19 | 2.50 | 283.5 | 213541.2 |
| v2_m2_only | 4/4 | 3 | 0 | 22 | 18 | 2.00 | 143.5 | 150589.1 |
| v2_fixed_three | 4/4 | 3 | 2 | 33 | 21 | 2.50 | 268.5 | 214294.9 |
| v2_no_user_state | 4/4 | 3 | 1 | 22 | 18 | 2.00 | 204.2 | 149210.4 |
| v2_no_epistemic_separation | 4/4 | 3 | 2 | 30 | 21 | 2.50 | 250.5 | 201864.7 |
| v2_no_failure_memory | 3/4 | 2 | 2 | 29 | 20 | 2.50 | 227.0 | 240941.2 |

The subset is a mechanism comparison, not a domain-wide ranking. Environment outcomes remain authoritative; a fluent answer cannot compensate for an unmet goal.
M3 is dynamically optional. Solving and verifying a case in M2 remains a pass; missing a targeted M3 branch is reported only as a coverage note.

## Interpretation of this subset

- `v2_m2_only` also passed 4/4 and used fewer model calls and less wall time. This subset does not demonstrate a success-rate advantage for M3; it supports keeping collaboration conditional instead of mandatory.
- `v2_fixed_three` also passed 4/4 but used one more model call and two more tool calls than `v2_full`. This is only a small observed cost difference, not a general efficiency claim.
- Removing user-state control or epistemic separation did not reduce endpoint success in these four Episodes. Their value therefore needs interaction- and semantic-safety-sensitive evaluation rather than outcome success alone.
- `v2_no_failure_memory` passed 3/4. In the failed retry Episode it stopped at `evidence_required` without verifying the goal. This single clean rerun is a causal candidate, not proof of a population-level memory benefit.
- A prior `v2_no_failure_memory` sample crossed a host sleep interval and is excluded from the table as infrastructure-invalid; it is retained separately for provenance.
