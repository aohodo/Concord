# M7 Evaluation Method

M7 tests outcome-grounded experience retention and reuse. It does not treat fluent answers, user
satisfaction, or model self-assessment as successful outcomes.

## Design basis

- Human behavior: recognition-primed decisions recall a plausible direction and mentally simulate
  it; case-based reasoning retrieves, reuses, revises and only then retains experience; after-action
  reviews compare expected and actual outcomes.
- Industry: SRE postmortems turn outcomes into structured, blameless improvement actions; online
  observations feed offline evaluation; candidate changes require controlled evaluation, a human
  release decision and rollback.
- Academic Agent memory: Reflexion, ExpeL and Voyager motivate experience reuse, while empirical
  memory studies warn that coarse experience-following can propagate incorrect trajectories.

References:

- Klein et al., *Rapid Decision Making on the Fire Ground*:
  https://journals.sagepub.com/doi/pdf/10.1518/155534310X12844000801203
- Aamodt & Plaza, *Case-Based Reasoning*:
  https://www.iiia.csic.es/~enric/papers/Aamodt_1994_Case.pdf
- Reflexion: https://arxiv.org/abs/2303.11366
- ExpeL: https://arxiv.org/abs/2308.10144
- Voyager: https://arxiv.org/abs/2305.16291
- Empirical Agent memory study: https://aclanthology.org/2026.acl-long.27/
- Google SRE Postmortem Culture: https://sre.google/workbook/postmortem-culture/
- Google SRE Canarying Releases: https://sre.google/workbook/canarying-releases/
- LangSmith evaluation types: https://docs.langchain.com/langsmith/evaluation-types

## Evaluation layers

1. Unit and integration contracts verify outcome authority, storage idempotency, self-reference
   exclusion, structured matching, downstream feedback and proposal lifecycle.
2. A 104-scenario injected-memory matrix spans eight domains and thirteen useful or hazardous
   memory conditions. Three variants produce 312 deterministic runs. The stronger coarse baseline
   already checks source eligibility, domain and overlapping state keys.
3. Three real `qwen3.8-flash` synthetic-environment Episodes check permission rejection, verified
   retention, cross-Case retrieval, current-world reverification and source-experience feedback.
4. M7.5 adds three cross-domain source→target chains and records retrieved, considered, adopted and
   verified attribution separately. It also records the negative result that reuse did not reduce
   calls or latency in this small sample.

Run:

```powershell
cd backend
D:\anaconda3\envs\concord\python.exe -m evaluation.m7_continual_improvement
D:\anaconda3\envs\concord\python.exe -m evaluation.m7_continual_improvement.real_chain
D:\anaconda3\envs\concord\python.exe -m pytest tests/test_continual_improvement.py -q
```

## Claim boundary

The injected matrix establishes retrieval-control behavior under known hazards. The three real-model
Episodes establish vertical wiring. Neither is large enough to claim an end-to-end success-rate gain;
that requires independent outcomes across longitudinal, diverse Cases.
