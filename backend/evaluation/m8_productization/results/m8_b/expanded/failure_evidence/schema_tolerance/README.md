# Schema-tolerance failure evidence

This checkpoint preserves the first expanded-catalog failure before remediation.
The model semantically separated an analogy from observations, but emitted two
near-schema values: one `EvidenceNeed` omitted its user-facing `question`, and another
used an empty `acquisition_actor`. Strict parsing rejected both complete turns and left
the Case in `formulation_error`.

The remediation is contract-level rather than case-specific: optional presentation text
can default empty, and an omitted/blank acquisition actor uses the model field's normal
default. The affected Episode is rerun through the same HTTP product path.
