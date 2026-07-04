# Roadmap to v1.0

Philosophy: dbparity is a tool of *proof*, so the priority is always
correctness → scale → convenience. A false "EQUIVALENT" is the worst bug this project can have.

## v0.2 — Correctness & Scale basics

- [x] Oracle: NUMBER → Decimal (not float!) and LOB → values — without this
  the verifier itself would be losing precision
- [x] NULL in PK: a dedicated `null_pk` category instead of a non-deterministic merge
- [x] Text PKs: a warning about sort-order/collation differences between engines
- [x] Parallel table comparison (`workers: N`, one connection per thread)
- [x] Live progress in the CLI
- [x] PyPI publishing workflow (on `v*` tags)

## v0.3 — Big tables (100M+ rows)

- [x] Bucketed DB-side hashes in a single scan (GROUP BY over PK ranges),
  streaming drill-down of the diverged buckets only; `strategy: auto|stream|hash`.
  Types outside the hash-safe set (float/datetime/bytes) → automatic fallback to stream.
  Imperfect canonicalization degrades speed, never correctness
- [x] Checkpoint/resume: atomic JSON state (config fingerprint,
  PK watermark, a partial slot per table), `--resume` in the CLI
- [x] Retry on network errors: `retry_attempts`/`retry_backoff_s`,
  a fresh pair of connections per attempt, resumption from the last watermark
- [x] Benchmark matrix in CI: `bench --json` + a workflow with regression
  thresholds and metrics published to the summary

## v0.4 — Oracle/MSSQL hardening

- [ ] Battle-testing on real Oracle instances (community issues)
- [ ] Encodings: AL32UTF8 vs UTF-8 edge cases, NCHAR/NVARCHAR2
- [x] MSSQL: a full adapter (ODBC 18, datetimeoffset converter,
  digest API with T-SQL canonicalization) + a live CI job (mcr mssql-server:2022)
- [x] Binary sort order for text PKs: `COLLATE "C"` (PG) /
  `NLSSORT BINARY` (Oracle) / `COLLATE BINARY` (sqlite) /
  `Latin1_General_BIN2` (MSSQL) — the warning replaced by a guarantee

## v0.5 — Parallel-run mode

- [x] Incremental runs over a watermark column (`incremental:` in the config,
  `--full` to reset): only changed rows are compared; missing/extra
  among them = dual-write drift; state carries the config fingerprint
- [x] Timeline report for a series of incremental runs: a journal in the state
  file, `dbparity history` (rich table + HTML with a drift-to-zero line chart)
- [x] Watch mode `dbparity watch`: cyclic incremental runs with a pause,
  until drift stays at zero (--stable N in a row),
  a green "safe to switch traffic" signal, exit codes for orchestration

## v0.9 — Release candidate

- [x] JSON report format stabilization: schema_version=1, evolution rules,
  the docs/report-format.md and docs/config-reference.md references,
  a golden test of the format
- [x] config.yaml format stabilization: frozen key sets
  (top level + rules) under a golden test, same evolution rules
- [x] `dbparity validate` — config checking without connecting to databases,
  aggregated errors with typo hints (done ahead of schedule)
- [x] Web console `dbparity serve`: a local UI (stdlib-only) — launch
  comparison runs from the browser, live progress, report serving
- [ ] Documentation: a site (mkdocs), recipes for typical migrations
  (Oracle→PG, MSSQL→PG, including the CIS-specific Postgres Pro)

## v1.0 criteria

1. ≥5 real migrations verified by the community/author, at least 1 of them with a 100M+ row table
2. Zero known classes of false "EQUIVALENT"
3. Oracle and MSSQL adapters covered by integration tests in CI
4. Config/report formats frozen (breaking changes → v2)
5. Published on PyPI, installable with `pip install dbparity`

## How to release

Tag `vX.Y.Z` on main → CI builds and publishes to PyPI
(requires a one-time Trusted Publisher setup on pypi.org:
project dbparity → Publishing → GitHub → repo `Nik-WEBJS/DBParity`,
workflow `release.yml`, environment `pypi`).
