# Contributing

Use the `dev` branch for integration work and keep changes scoped to one
observable failure or product capability. Do not add keyword patches for
natural-language behavior; prefer structured state, tool contracts and tests
that exercise the real call chain.

Before opening a pull request:

```powershell
.\scripts\verify.ps1
```

Never commit `.env`, local databases, logs, model traces, private documents or
real user data. Public evaluation fixtures must be synthetic or demonstrably
de-identified. Changes that can write to an external system require explicit
authorization, idempotency, audit and integration tests at that boundary.
