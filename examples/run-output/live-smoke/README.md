# Live smoke run

This is a real run against `/v1/sessions` on 2026-08-26.
It ran three read-only audit tasks; no PRs were opened by design.
ACU cost is unavailable on v1 and is not represented as zero.
The third task had no path to its answer and checks that a session reports
`blocked` with named blockers rather than inventing one.
Three tasks make this a smoke test, not a benchmark.
