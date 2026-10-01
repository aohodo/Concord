# Sparse optional-field failure evidence

This checkpoint preserves a multi-issue Episode where the model returned a `null`
unmapped span and an empty user-state candidate containing only the field name. Strict
parsing discarded the otherwise grounded observations and the next turn could not
reconstruct the situation.

The remediation is evidence-preserving and domain-independent: null/empty spans and
user-state candidates without both a value and a direct evidence quote are discarded.
No user trait is inferred to fill the missing values. M1 states that require another user
turn are also treated as observable evaluation endpoints once the scripted turns are
exhausted, avoiding an idle 180-second poll.
