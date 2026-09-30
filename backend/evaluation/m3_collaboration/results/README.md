# M3 real-model smoke results

These JSONL files are sanitized outputs from the first M3 vertical-slice smoke tests using `qwen3.8-flash` with proxy inheritance disabled.

- `real_qwen38_low_value.jsonl`: a low-value collaboration request that should return to M2 without recruiting workers.
- `real_qwen38_high_risk_review.jsonl`: a high-risk proposal that should recruit independent reviewers and require additional evidence.

The fixed-topology count in each row is a declared coordination-cost baseline. It was not executed as a quality-control arm, so the files must not be interpreted as proving end-to-end quality superiority over a fixed multi-agent system.

All scenarios and infrastructure observations in these files are synthetic. Local debug failures, credentials, environment files, and private development notes are excluded.

Phase 2 artifacts:

- `m3_phase2_topology_baseline.jsonl`: actually executed M2-only, fixed-three, and adaptive runs over the eight boundary cases.
- `m3_phase2_topology_judged.jsonl`: the same immutable raw rows with an auxiliary independent LLM judgment.
- `m3_phase2_longitudinal.jsonl`: 36 imperfect-input episodes, including 12 mid-flight Case corrections.
- `m3_phase2_longitudinal_rerun.jsonl`: targeted reruns of the eight first-run failures after structural fixes; it does not overwrite the first run.
- `m3_phase2_longitudinal_final.jsonl`: latest result per episode, produced by the report merger from immutable raw runs.
- `m3_phase2_vertical_smoke.jsonl`: retained first permission-boundary smoke that exposed redundant M3 work.
- `m3_phase2_vertical_smoke_final.jsonl`: repaired permission-boundary chain; M3 stops without a model or worker call.
- `m3_phase2_verified_feedback_smoke.jsonl`: real M3 advice -> M2 action/verification -> assignment-scoped reliability credit.
- `topology_summary.csv` / `topology_summary.json`: compact comparison data.
- `M3_PHASE2_REPORT.md`: generated interpretation with explicit validity limits.
- `experiment_manifest.json`: non-sensitive runtime metadata.

Current final observations:

- adaptive / fixed-three / M2-only boundary passes: `8/8`, `6/8`, `3/8`;
- auxiliary judge: adaptive `8 PASS`; fixed-three `2 PASS / 4 PARTIAL / 2 FAIL`;
- longitudinal first run `28/36`, targeted rerun `8/8`, merged final `36/36`;
- all `12/12` mid-flight corrections rejected stale initial collaboration results;
- the verified-feedback smoke credited exactly the assignment cited by downstream M2 tools.

These are synthetic product tests. They demonstrate the measured behavior of this build, not general multi-agent superiority or human-expert correctness.
