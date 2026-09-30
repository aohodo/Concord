# M5 M3 System-Level Topology Comparison

`environment goal` and `governance pass` are deliberately separate: a tool action may solve the simulated environment while later orchestration regresses the Case state or remains unfinished.

| variant | governance pass | environment goal | regressed after resolved | M1 errors | M3 reached | model calls | tool calls | avg latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| v2_m2_only | 8/8 | 8/8 | 0 | 0 | 0 | 50 | 43 | 117125.2 |
| v2_always_on_fixed_three | 0/8 | 8/8 | 7 | 0 | 8 | 73 | 44 | 330838.9 |
| v2_gated_fixed_three | 6/8 | 8/8 | 2 | 2 | 1 | 50 | 42 | 118384.7 |
| v2_full | 8/8 | 8/8 | 0 | 0 | 1 | 54 | 43 | 133566.9 |

`always_on_fixed_three → gated_fixed_three` isolates collaboration-gate savings. `gated_fixed_three → v2_full` isolates dynamic team-shape and stopping. `m2_only → v2_full` measures sparse recovery value.
