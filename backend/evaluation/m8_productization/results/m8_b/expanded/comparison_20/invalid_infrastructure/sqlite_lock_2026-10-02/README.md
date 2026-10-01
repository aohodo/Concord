# Excluded infrastructure run

These raw artifacts are retained for auditability but are excluded from the
M8-B mechanism comparison. Concurrent evaluation requests exposed contention
between independent SQLite adapters sharing the runtime database. The affected
`v2_no_epistemic_separation` and `v2_no_failure_memory` rows therefore measured
`database is locked` failures rather than either ablated mechanism.

The runtime was corrected by giving adapters for the same SQLite path one
process-local access coordinator, enabling WAL, and using a bounded busy
timeout. Both affected variants were then rerun against a clean isolated
runtime database. Only the replacement rows are included in the active report.
