# M5 System Call-Chain Audit

This audit separates implementation presence, runtime activation, downstream consumption,
and outcome evidence. A field or class existing in the repository is not evidence that it
affects product behavior.

## Product path

| capability | producer | transfer | consumer / observable behavior | evidence | status |
|---|---|---|---|---|---|
| Shared problem formulation | `/chat` user turns | `ResolutionContext` | M2 receives goal, evidence, constraints and current user state | 22 `m1_turn_completed` followed by 22 M2 runs | observed end-to-end |
| Current-turn user state | M1 grounded `CaseUserState` | `resolution_context.user_state` | M2 changes latency/burden weights and response load | 4 activations, 4 behavior changes, 4 outcomes | effectiveness evaluable |
| M2 closed loop | provisional explanation and tool ranking | `ToolInvocation` / `VerificationRequest` | runtime executes, observes and independently verifies environment state | 7 resolved + 1 correct human boundary in long run | observed end-to-end |
| Action safety | M1 action constraints + tool capability contract | `ExecutionContext` | permission, risk, parameter, idempotency and audit enforcement | permission boundary Episode + deterministic runtime tests | observed at boundary |
| Failure memory | failed canonical call history | M2 graph state | identical failed call blocked until conditions change | 22 evaluations, 0 activations in this batch | evaluated, not triggered; no effectiveness claim |
| M3 invocation gate | M2 status / experimental policy | durable Job payload | avoids collaboration or enqueues recoverable work | 4 enqueues across 2 long-run Cases | observed end-to-end |
| Dynamic M3 internal control | coordinator plan | assignments and contributions | recruits a minimal team and applies stop logic | 1 activation changed recruitment | behavior observed only |
| M3 advice consumed by M2 | `M2ResumePacket` | original M2 thread | M2 acts on advice and independently verifies | 0 `m2_resumed_after_collaboration` in this long-run batch | not outcome-evaluable from this batch |
| Human/permission boundary | M2/M3 boundary result | Case lifecycle | stops instead of fabricating production authority | permission Episode ended `human_required` | observed end-to-end |

## Long-horizon governance path

| capability | durable source | consumer | evidence | status |
|---|---|---|---|---|
| Case/Event persistence | SQLite snapshot + append-only event rows | API reads and startup loader | process restart smoke plus restore test | observed and test-covered |
| Recoverable jobs | SQLite Job with lease and heartbeat | `DurableCaseJobWorker` | 4 jobs, 3 completion events in captured snapshot; job state separately persisted | observed |
| Exactly-once simulated writes | SQLite idempotency result | tool runtime retry | commit-then-response-loss test records one write and replay | test-covered |
| Durable fault injection | SQLite ordered fault queue | parallel tool bootstrap/action calls | concurrent consume and restart tests | test-covered |
| Revision invalidation | new goal/evidence/constraints | stale-job filter and M3 result guard | 1 real `stale_jobs_invalidated` plus deterministic tests | observed and test-covered |
| Compact context | current structured Case | long-run prompt/runtime summary | present in 8/8 long Episodes | observed |
| Lifecycle control | Case control API | registry + queued jobs | pause/resume worker test; cancel/reopen/wait/budget registry test | test-covered, not real-model-triggered |
| Resource budget | Case budget limits and measured usage | worker pre-collaboration check | deterministic budget test | test-covered, not real-model-triggered |
| Terminal-state preservation | transient M1 parse failure | Case registry | preserves last verified terminal state and records rejected turn | deterministic regression test | test-covered; real-model smoke did not trigger this exact branch |

## Strict conclusions

1. The M5 durability path is in the real HTTP product chain; it is not a disconnected model layer.
2. User-state control and M3 gating have producer, consumer, behavior change, and outcome records.
3. Failure memory was evaluated but not activated, so this dataset says nothing about its success-rate benefit.
4. Dynamic M3 recruitment executed, but its activated sample did not return through an observable M2 resume in this batch. The batch therefore cannot prove that dynamic collaboration caused the final success.
5. Always-on fixed collaboration reached all eight Cases and materially increased calls, queueing, and terminal-state regression. This supports sparse gating, not a universal claim that every dynamic team beats every fixed team.
6. Lifecycle control and budget enforcement are implemented and deterministically tested but were not activated by the eight real-model Episodes. Their runtime benefit remains a later fault/operations experiment, not a current résumé metric.
7. A latest-code two-Case smoke passed 1/2. The non-pass had already restored the environment, then accepted a new user success criterion (no duplicate start) that the simulator could not evidence, so `evidence_required` is not treated as an implementation regression. An earlier 2/2 run accidentally hit an old Uvicorn process and is excluded.
