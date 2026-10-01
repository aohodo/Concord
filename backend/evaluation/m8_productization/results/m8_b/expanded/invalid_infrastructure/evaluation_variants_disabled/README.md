# Invalid evaluation-permission sample

This checkpoint preserves an expanded-catalog run started after a service restart where
`CONCORD_ENABLE_EVALUATION_VARIANTS=true` was accidentally omitted. The public chat
entry therefore intentionally discarded evaluation-only `simulation:act` permissions.
M2 observed the environment, correctly found the action unavailable under its effective
runtime authority, and stopped at `human_required`.

The initial stale-constraint hypothesis was rejected after tracing permission derivation
at the API boundary. This bundle is infrastructure-invalid for Agent success metrics;
no business-logic change was made for these outcomes. Affected Episodes are rerun with
the declared evaluation profile enabled.
