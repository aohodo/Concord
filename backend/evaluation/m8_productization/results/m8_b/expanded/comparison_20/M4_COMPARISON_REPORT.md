# M4 Mechanism Comparison

| variant | pass | M3 | model calls | tools | avg turns | first progress ms | M1 ms | M2 ms | avg episode ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v2_full | 20/20 | 2 | 131 | 106 | 2.40 | 68.3 | 26885.9 | 20331.5 | 118438.7 |
| v2_m2_only | 17/20 | 0 | 129 | 98 | 2.50 | 65.1 | 38178.2 | 19944.0 | 143800.5 |
| v2_fixed_three | 20/20 | 4 | 155 | 111 | 2.50 | 67.4 | 29889.1 | 21550.1 | 139257.8 |
| v2_no_user_state | 17/20 | 3 | 156 | 108 | 2.50 | 66.5 | 37796.9 | 22313.1 | 164963.2 |
| v2_no_epistemic_separation | 18/20 | 2 | 143 | 105 | 2.50 | 65.2 | 39891.0 | 20929.0 | 156461.4 |
| v2_no_failure_memory | 17/20 | 2 | 144 | 110 | 2.40 | 67.6 | 26016.6 | 24586.0 | 130681.4 |

The subset is a mechanism comparison, not a domain-wide ranking. Environment outcomes remain authoritative; a fluent answer cannot compensate for an unmet goal.
M3 is dynamically optional. Solving and verifying a case in M2 remains a pass; missing a targeted M3 branch is reported only as a coverage note.

## Interpretation of this subset

Interpret effects as paired observations on the same Episodes. Endpoint success, latency, model/tool cost, interaction burden and call-chain activation are kept separate; an untriggered mechanism is not labelled ineffective.

## Paired observations versus full

| variant | pairs | success worse/same/better | latency faster/slower | calls lower/higher |
|---|---:|---:|---:|---:|
| v2_fixed_three | 20 | 0/20/0 | 6/14 | 4/7 |
| v2_m2_only | 20 | 3/17/0 | 11/9 | 6/4 |
| v2_no_epistemic_separation | 20 | 2/18/0 | 8/12 | 3/7 |
| v2_no_failure_memory | 20 | 3/17/0 | 8/12 | 4/8 |
| v2_no_user_state | 20 | 3/17/0 | 8/12 | 3/9 |

`stratified_results.csv` exposes domain and user-condition slices; `paired_results.csv` preserves every same-Episode delta.
