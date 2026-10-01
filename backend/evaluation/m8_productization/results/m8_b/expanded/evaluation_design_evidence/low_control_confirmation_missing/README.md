# Incomplete low-control user script

This checkpoint preserves a false-negative evaluation design. The low-control user
explicitly allowed only read-only checks before a safe action. Concord performed the
checks and stopped at `waiting_for_user`, but the scripted Episode ended without the
confirmation it had made necessary while still requiring a resolved outcome.

All low-control Episodes now include a third turn granting conditional confirmation for
resettable, reversible simulation actions while retaining a stop condition for real
production authority. Existing low-control rows are rerun so the catalog and raw traces
remain aligned.
