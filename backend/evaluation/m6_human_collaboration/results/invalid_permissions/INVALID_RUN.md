# Invalid M6 run: evaluation permissions disabled

This batch is retained for auditability and excluded from M6 outcome claims.

The HTTP evaluator supplied simulated-write permissions, but the server was started with
the safe default `CONCORD_ENABLE_EVALUATION_VARIANTS=false`. The runtime therefore removed
those permissions, correctly stopped at the authorization boundary, and produced three
`human_required` outcomes. This is an evaluation-environment error, not a valid product
comparison and not evidence for changing prompts, cases, or runtime authorization rules.
