# Concord Current Evidence

Updated: 2026-09-30. This file is the current evidence index; milestone reports remain historical
records and must not be silently rewritten to imply stronger results.

## Verified on current HEAD

- Backend regression: `198 passed`.
- Frontend production build: passed with Vite 7.3.5.
- M7.5 deterministic memory controls: 104 scenarios × 3 variants = 312 runs.
  - no memory: 72/104 correct, 0/32 useful memories retrieved;
  - coarse structured baseline: 64/104 correct, 32/32 useful retrieved, 40/72 unsafe retrieved;
  - structured verified: 104/104 correct, 32/32 useful retrieved, 72/72 hazards rejected.
- M7.5 real `qwen3.8-flash` source→target chains: 3 domains, 3/3 source outcomes and 3/3 target
  outcomes independently verified; every target completed retrieved → considered → adopted →
  verified attribution and re-executed five tool calls.
- Real-chain mean latency: source 27.319 s; experience-enabled target 33.249 s. Both used two model
  calls and five tool calls per Case.

## What these results support

- M2 will not resolve a Case merely because any observation exists: declared user success criteria
  must be covered by runtime-valid evidence references.
- Durable experience excludes raw tool arguments/results, persists only tool-contract allowlisted
  fields, redacts common secrets and personal contact values, and enforces size limits.
- Experience applicability now rejects conflicting state values, unavailable capabilities,
  insufficient permissions, tool-contract version drift and expired sources.
- Experience outcome credit is based on an executed tool step carrying an adopted source ID, not a
  final model self-report.

## What these results do not support

- They do not show that experience improves success rate, latency or tool cost. The current real
  sample shows correct reuse wiring but no efficiency gain and higher target latency.
- They do not establish production security, real-system integration, broad multimodal quality or
  statistically powered user-experience conclusions.
- The three real chains are smoke tests, not a representative longitudinal benchmark.

## Reproduction

```powershell
cd E:\personal_project\Concord
D:\anaconda3\envs\concord\python.exe -m pytest backend/tests -q
cd backend
D:\anaconda3\envs\concord\python.exe -m evaluation.m7_continual_improvement
D:\anaconda3\envs\concord\python.exe -m evaluation.m7_continual_improvement.real_chain
cd ..\frontend
npm run build
```

Artifacts:

- `backend/evaluation/m7_continual_improvement/results/latest/`
- `backend/evaluation/m7_continual_improvement/results/m7_5_real/`
