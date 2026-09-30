# M6.5 Real-model Runtime Batch

- Episodes: 10
- Control-contract pass: 10/10
- Repetitions: 1
- Judge: none; pass/fail only checks runtime and explicit control contracts

Semantic response quality remains in the raw records for later human or independent-judge review. It is not folded into this contract pass rate.

## Aggregate runtime signals

- Mean measured stage time: 67.15s; median: 63.45s.
- Mean M1 time: 38.92s; mean M2 time: 28.18s.
- Expression depth: 6 minimal, 3 guided, 1 expert.
- Initiative: 6 agent-led, 2 shared, 2 step-by-step.
- Question budget respected: 10/10.

`elapsed_ms` in this first batch includes time waiting for the concurrency semaphore for
later queued cases. Stage timings are the valid per-request latency attribution. The runner
now records queue wait and server request latency separately for future batches; historical
raw values remain unchanged.

## Separate semantic pressure review

Manual negative review of the initial outputs found 5 PASS, 4 PARTIAL, and 1 FAIL. The FAIL
was not a model-knowledge failure: M2 had formed the useful provisional result, but the
waiting-for-user finalizer replaced the result-first bridge with structured questions and
actions. The finalizer now preserves the concise result before the bounded request. A single
real-model rerun of the failed case passed and is preserved in `fix_verification_raw.jsonl`.

This corrective rerun does not replace or delete the original failure.
