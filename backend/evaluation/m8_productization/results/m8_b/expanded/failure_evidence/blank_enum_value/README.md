# Blank enum failure evidence

This checkpoint preserves a recovery-path failure where an otherwise grounded action
candidate used an empty string for `outcome`. The compact semantic recovery parser
correctly refused to treat it as success, failure or partial progress.

The contract now maps missing, blank or unknown action outcomes to `unknown`. This is
the conservative epistemic value and does not invent a result. The affected Episode is
rerun through the same API path.
