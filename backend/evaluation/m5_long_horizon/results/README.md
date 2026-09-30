# M5 Result Provenance

All folders are retained to avoid rewriting failed or invalid experiments as successes.

| folder | use in conclusions | note |
|---|---|---|
| `final/` | yes | eight long-horizon `v2_full` Episodes; 8/8 governance pass |
| `topology/` | yes | four real-model system variants over the same eight-Episode subset |
| `smoke_valid3/` | yes, smoke only | latest valid pre-experiment smoke; 1/1 |
| `post_fix_gated_latest/` | yes, qualified | latest-code two-Case smoke; 1/2, with one new success criterion lacking simulator evidence |
| `post_fix_gated/` | no | evaluator accidentally connected to an older Uvicorn child process that had not exited |
| `smoke/` | no | evaluation variants were disabled, so the requested permissions were not accepted |
| `smoke_valid/`, `smoke_valid2/` | no | development runs before the persistent fault-queue concurrency fix |

The topology report separates environment-goal satisfaction from terminal Case-governance
success. Raw JSONL is authoritative; summary files are derived artifacts.
