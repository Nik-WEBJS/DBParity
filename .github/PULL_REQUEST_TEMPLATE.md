<!-- Thanks! Please link the related issue; for non-trivial changes an issue first is appreciated. -->

## What & why

## Checklist

- [ ] `pytest tests/ -v` is green locally, and the change is covered by tests
      (bug fixes come with a regression test)
- [ ] Benchmark has not regressed — `bench.yml` gates every PR; if you touched
      the compare/normalize hot path, run `python3 bench/bench.py 200000` locally
- [ ] New config keys are documented in `docs/config-reference.md`
      (report changes → `docs/report-format.md`)
- [ ] JSON report format is intact — `tests/test_report_schema.py` passes
      (format is frozen at `schema_version: 1`; evolution rules in `docs/report-format.md`)
- [ ] Commits / PR title follow Conventional Commits (`feat:`, `fix:`, `docs:`, …)

## Notes for the reviewer

<!-- Anything that touches comparison semantics deserves a call-out:
     a false "EQUIVALENT" is the worst bug this project can have. -->
