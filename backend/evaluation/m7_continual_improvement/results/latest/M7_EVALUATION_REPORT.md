# M7 Verified Experience Evaluation

This control-plane evaluation uses injected verified, unverified, deprecated, cross-domain and state-mismatched experiences. It measures retrieval safety, not real-world task-resolution uplift.

| variant | correct | useful hits | unsafe retrievals | safe rejection rate |
|---|---:|---:|---:|---:|
| no_memory | 72/104 | 0/32 | 0/72 | 72/72 |
| coarse_structured | 64/104 | 32/32 | 40/72 | 32/72 |
| structured_verified | 104/104 | 32/32 | 0/72 | 72/72 |

## Failure distribution

- `coarse_structured` / `capability_mismatch`: 8
- `coarse_structured` / `expired_source`: 8
- `coarse_structured` / `permission_mismatch`: 8
- `coarse_structured` / `state_value_conflict`: 8
- `coarse_structured` / `tool_version_mismatch`: 8
- `no_memory` / `boundary_handoff`: 8
- `no_memory` / `exact_success`: 8
- `no_memory` / `failure_avoidance`: 8
- `no_memory` / `partial_success`: 8

## Interpretation boundary

A structured-memory pass proves that the retention and retrieval controls reject the injected hazards. It does not prove that experience improves end-to-end resolution success; that claim requires real longitudinal Episodes with independent outcome verification.
