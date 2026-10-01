# Ungrounded action ID failure evidence

This directory preserves the pre-fix real-model artifacts for
`write_committed_response_lost__frustrated_repeat`.

## Observed failure

The planner had already received the runtime action catalog, which exposed only
`restore_service`, but later requested the invented action ID
`route_to_backup`. The scenario runtime correctly returned
`UNKNOWN_SIMULATED_ACTION`. The tool runtime then consumed an injected
`network_lost_after_commit` fault unconditionally and replaced that rejection
with an uncertain-commit timeout. The Case therefore remained degraded while
the reasoning trace incorrectly treated a write as potentially committed.

## Structural correction

1. A simulated state-changing action is accepted by M2 only when its ID is
   present in a successful `simulation_list_actions` result from the same Case.
2. An after-call fault is consumed only after a succeeded, pending, or partial
   provider result. Failed or rejected calls cannot cross the commit boundary.

Both rules are enforced by runtime contracts and regression tests. No domain
keyword, Episode ID, or expected answer was added to production logic.
