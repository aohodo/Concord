# M5 Control Call-Chain Audit

A mechanism is never judged ineffective merely because it was not triggered.

| mechanism | evaluated | activated | behavior changed | downstream outcome | classification | effectiveness evaluable |
|---|---:|---:|---:|---:|---|---|
| case_user_state | 22 | 4 | 4 | 4 | ACTIVATED_AND_CONSUMED | True |
| failure_memory | 22 | 0 | 0 | 0 | EVALUATED_NOT_TRIGGERED | False |
| m3_invocation_policy | 22 | 4 | 4 | 2 | ACTIVATED_AND_CONSUMED | True |
| adaptive_m3 | 4 | 1 | 1 | 0 | ACTIVATED_AND_CONSUMED | False |

Behavior activation alone is insufficient. Effectiveness is evaluable only
when the downstream consumer also records outcome evidence; cost/benefit
claims still require a paired control.
