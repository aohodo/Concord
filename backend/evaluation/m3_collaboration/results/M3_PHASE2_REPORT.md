# M3 Phase 2 Comparison and Ablation Report

## Scope

This report compares an M2-only control, an actually executed fixed three-agent topology, and adaptive M3. Boundary pass counts are based on declared synthetic case expectations; they are not human expert ground truth. Raw rows remain the source of truth.

## Topology ablation

| topology | boundary passes | judge P/Pt/F | avg model calls | avg agents | avg latency ms | avg input tokens | avg output tokens | measured cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| adaptive | 8/8 | 8/0/0 | 2.75 | 1.12 | 50744.12 | 5258.62 | 3665.5 | 0.28 |
| fixed_three | 6/8 | 2/4/2 | 4.12 | 3 | 50511.75 | 7621.88 | 4917.75 | 0.33 |
| m2_only | 3/8 | 3/0/5 | 0.0 | 0 | 0.0 | 0.0 | 0.0 | 0.0 |

## Longitudinal episodes

- Episodes: 36
- Boundary passes: 36/36
- Mid-flight revision invalidation: 12/12
- Verified/simulated outcome events: 39
- Total model calls: 137
- Total input tokens: 301236
- Total output tokens: 223179
- Average model calls: 3.81
- Average latency: 89114.58 ms

## Interpretation limits

- The longitudinal catalog is synthetic and intentionally contains fragmented, mistaken, urgent, and corrected reports.
- `novelty_proxy` measures structural non-duplication, not truth. Truth credit is only written after a downstream verification event.
- Shared-source agreement is flagged as correlated evidence and is not treated as independent corroboration.
- M3 advice remains `advisory_until_m2_verifies`; M3 never executes environment writes.
- The comparison isolates collaboration topology. It does not prove universal superiority across domains or models.

## Product-chain smoke tests

| case | passed | M3 status | M2 status | M3 model calls | verified feedback |
|---|---:|---|---|---:|---:|
| m3-vertical-permission_boundary | true | human_required | not_resumed | 0 | 0 |
| m3-vertical-verified-feedback | true | evidence_required | resolved | 3 | 1 |
