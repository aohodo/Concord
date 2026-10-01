# Unknown claim-type failure evidence

This checkpoint preserves a valid expanded-catalog failure. The provider represented a
user's preferred procedure as a claim with the near-schema type
`procedure_preference`. The contract only accepts observation, action or hypothesis,
so strict parsing discarded the otherwise useful turn.

The remediation is epistemically conservative and domain-independent: an unknown
provider claim label is normalized to `hypothesis`, never to an observation or verified
fact. The affected Episode is rerun through the same HTTP product path.
