# M8-B Active Call-Chain Audit

This matrix distinguishes implemented code from mechanisms that measurably affect the default
product path. A row passes only when a producer feeds a consumer and a behavior-level test covers
the boundary; the existence of a model field alone is not evidence of activation.

| mechanism | producer | consumer / effect | behavioral evidence |
|---|---|---|---|
| M1 shared formulation | turn interpreter + interaction policy | `ResolutionContext` or bounded clarification | `test_problem_formulation.py` |
| user-state adaptation | Case-conditioned user state | question count, response burden, terminology and progress cadence | `test_problem_formulation.py`, `test_human_collaboration.py` |
| failed-action memory | M2 tool history | blocks identical failed calls until retry conditions change | `test_adaptive_resolution.py` |
| independent verification | tool `VerificationRequest` | prevents `resolved` until declared criteria bind to observations | `test_adaptive_resolution.py`, `test_continual_improvement.py` |
| dynamic collaboration | M2 handoff + marginal-gain policy | recruit, shrink or skip M3; advisory returns to M2 | `test_adaptive_collaboration.py`, `test_m6_5_coordination.py` |
| verified experience | M7 outcome verifier | retains only independently supported strategies | `test_continual_improvement.py` |
| runtime-safe reuse | request tool registry + policy | rejects experience requiring unavailable capability, permission or contract version | `test_adaptive_tool_runtime.py`, `test_continual_improvement.py` |
| working memory | Redis or in-process TTL adapter | improves conversational continuity only | `test_conversation_memory.py` |
| historical recall | SQLite FTS recall index | prompt context explicitly marked unverified and possibly stale | `test_conversation_memory.py`, `test_sqlite_text_index.py` |
| durable truth | SQLite Case/events/checkpoints/outcomes | recovery, ownership, stale-work invalidation and audit | `test_case_orchestration.py`, `test_case_events.py`, `test_m4_end_to_end.py` |

## Default product boundary

The default `/chat` path is M1 → M2 → conditional M3 → verified M7 observation. Retired
third-party baseline code and evaluation routes are not part of the public source tree or OpenAPI.

Redis loss can remove recent conversational convenience but cannot rewrite durable Case truth.
Full-text matches are candidate recollections, never verified evidence. Tool observations and
SQLite Case state remain the authority for resolution and learning.
