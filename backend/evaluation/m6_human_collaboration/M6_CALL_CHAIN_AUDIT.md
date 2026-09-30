# M6 Call-chain Audit

This audit asks whether each M6 design is on the executable product path. A model or field
definition alone does not count as implementation.

| Capability | Input / decision | Runtime effect | Persistence / observation | Product surface | Evidence |
|---|---|---|---|---|---|
| Fast acknowledgement | `/chat` with stable `case_id` | records before the first model call | `user_turn_received` Case event | Console polling shows intake while the request is running | 4/4 real Episodes |
| Current-Case user adaptation | M1 semantic `user_state` plus explicit Case overrides | compiles expression depth, initiative, progress cadence and effort budget | `human_collaboration` in snapshot and `human_effort_accounted` events | short default answer, expandable details and preference controls | 12/12 contracts; 4/4 real wiring |
| Human effort accounting | formulation / M2 result / presentation | counts questions, requested actions, primary/deferred chars and visible progress | durable `HumanEffortUsage` | Console budget panel | unit and durable-reload tests |
| Mixed initiative | control and preference APIs; new user turns | pause/resume/cancel/reopen, update preferences, revise M1 and invalidate dependent work | lifecycle and preference events; Case revision | Console controls | transition tests and real multi-turn Episodes |
| In-flight interruption | pause or cancel while a durable Job is active | cancels handler; lease fencing prevents stale commit | waiting/cancelled Job state remains authoritative | Console immediately reflects control | stale-worker fencing test |
| Durable timeout | Job execution envelope | cancels timed-out handler and marks terminal state | `JOB_EXECUTION_TIMEOUT`, `timed_out`, failure event | Console terminal status | worker timeout test |
| Multimodal evidence | images, text, logs, JSON/CSV, audio transcript | renders source-marked evidence into M1; image uses vision extraction | `EvidenceArtifact` with source, method, uncertainty and epistemic status | attachment composer and evidence count | evidence and durable-reload tests |
| No fabricated audio understanding | audio without transcript | returns an unprocessed artifact instead of invented content | uncertainty explicitly preserved | Console shows artifact | unit test |
| Visible progress | M1/M2/M3/tool/Job events | exposes milestones without hidden reasoning | ordered, paginated Case events | timeline | 4/4 real Episodes |
| Sparse M3 | M2 collaboration trigger | only schedules M3 when single-owner resolution hits a boundary | atomic Case event + durable Job | phase and Job cards | real permission-boundary Episode |
| Safe traces | Case identity and ownership | rejects cross-Case access; global trace debug is opt-in | tool audit remains authoritative | trace panel | API wiring and existing trace tests |
| Python-only product runtime | Python API configuration | Java is absent from Vite proxy and Compose services | Java remains only under `reference/` | no backend switch in Console | production build and Compose validation |

## Data flow

```text
Vue Case Console
  -> POST /chat (text + attachments + stable Case ID)
  -> record_turn_received
  -> M1 semantic formulation and current user-state evidence
  -> record_formulation + dependency invalidation
  -> HumanCollaborationState / HumanEffortBudget
  -> M2 adaptive resolution
     -> tools -> observations -> belief update -> verification
     -> optional atomic durable M3 Job -> M2 resume
  -> layered response + effort accounting
  -> SQLite Case/Event/Job snapshot
  -> GET /cases/{id}/console polling
```

## Findings

No M6 capability above is an orphan schema field. Every claimed feature reaches an API,
runtime behavior, durable observation, or UI consumer. The first real-model batch was excluded
because evaluation permissions were disabled; the authorization boundary behaved correctly and
the batch is retained under `results/invalid_permissions/`.

The valid real-model batch passed 4/4 targeted longitudinal Episodes, but averaged 139.51 seconds.
M6 therefore proves interaction-control wiring and outcome preservation, not acceptable production
latency or population-level user satisfaction. Audio transcription, production identity, distributed
workers, larger multimodal datasets and latency reduction remain explicit later-milestone work.
