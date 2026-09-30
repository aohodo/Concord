# M6.5 Targeted Real-model Smoke Report

## Scope

This directory records twelve synthetic, non-sensitive Episodes executed through the public
`POST /chat` boundary with the configured real `qwen3.8-flash` model and proxies disabled.
Two smoke Episodes cover text and generated-PNG evidence. A ten-case batch covers fragmented,
half-expert, frustrated, low-control, expert, procedural-only, cross-domain-transfer,
low-patience, multi-issue, and uncertain-goal inputs. No real user screenshot is included.

These runs validate wiring, epistemic handling, progress visibility, interaction budgets, and
stage-level latency. Twelve targeted Episodes remain too small and too scenario-concentrated
to estimate population-level or cross-domain competence.

## Results

| Episode | input | terminal phase | total | vision | M1 | M2 |
|---|---|---|---:|---:|---:|---:|
| `m65-text-001` | text | `waiting_for_user` | 52.922s | - | 33.625s | 18.953s |
| `m65-image-001` | generated PNG | `waiting_for_user` | 98.438s | 7.172s | 62.250s | 28.938s |

Both runs respected a one-question interaction budget and stopped for missing user evidence.
The image run extracted the visible VPN 691 authentication error and persisted it as an
`unverified_visual_observation`; it did not convert OCR/vision output into verified fact.

The measurements attribute nearly all visible delay to serial model inference in vision,
M1, and M2. Memory, presentation, accounting, and persistence were not material contributors
in these two runs. This supports optimizing model-call structure without deleting evidence,
verification, or Case-state semantics.

The additional runtime batch passed explicit control contracts in 10/10 cases. A separate
manual negative review rated the initial response semantics as 5 PASS, 4 PARTIAL, and 1 FAIL.
The one failure exposed a finalization bug that discarded an already-formed result-first bridge
when a user question was also present. The original failure and the successful post-fix real
rerun are both retained.

## Remaining evidence boundary

M6.5 now has the planned 12-case targeted batch, but this is a release-readiness sample rather
than evidence of behavioral generalization. Existing M4/M6 longitudinal datasets remain useful
historical baselines, but they predate the M6.5 request-generation and interaction-control
changes. Broader domains and repeated statistical estimates belong to later evaluation work.
