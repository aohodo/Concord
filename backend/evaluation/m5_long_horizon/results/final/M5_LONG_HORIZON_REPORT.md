# M5 Long-Horizon Case Governance Report

- Episodes: 8
- Outcome + governance pass: 8/8
- Resolved: 7
- Human-required boundary: 1
- Persisted lifecycle events: 130
- Durable asynchronous jobs: 4

| episode | outcome | events | jobs | governance failures |
|---|---|---:|---:|---|
| vpn_stale_credential__self_correction | resolved | 15 | 0 | - |
| saas_webhook_secret__fragmented_novice | resolved | 15 | 0 | - |
| education_sso_clock_drift__deadline_result_first | resolved | 10 | 0 | - |
| logistics_spooler_backlog__self_correction | resolved | 15 | 0 | - |
| write_committed_response_lost__self_correction | resolved | 15 | 0 | - |
| asynchronous_recovery__deadline_result_first | resolved | 10 | 0 | - |
| permission_boundary__fragmented_novice | human_required | 30 | 3 | - |
| specialist_recovery_after_tool_failures__self_correction | resolved | 20 | 1 | - |

Restart recovery and exactly-once replay are additionally exercised by deterministic integration tests that close and reopen the SQLite runtime. Environment outcome remains the success authority.
