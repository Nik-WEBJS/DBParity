# Changelog

## 0.9.0 — 2026-07-04

Full English localization for the international audience.

- **i18n**: every user-facing surface is now English — CLI output and
  warnings, HTML report and drift-timeline templates, web console UI,
  config validation messages, engine notes in reports
- All code comments, docstrings and test suites translated; demo data
  and the Pages landing are English; the Russian README remains as
  README.ru.md
- Report/config format guarantees unchanged (schema_version stays 1 —
  keys were always English; only human-readable strings changed)

## 0.8.0 — 2026-07-03

- **feat(cli)**: `dbparity watch` — cutover-night mode: incremental
  comparison runs every N seconds until drift stays at zero `--stable` times
  in a row; a green "safe to switch traffic" signal, exit code 0/1/2 for
  orchestration. This closes out the entire v0.5 (parallel-run) block of the
  roadmap

## 0.7.0 — 2026-07-03

- **feat(cli)**: `dbparity history` — a dual-write drift timeline: a journal
  of incremental runs kept in the state file (capped at 500 entries), a rich
  table with the trend, and a self-contained HTML page (line chart of
  total_diffs per table, verdict "ZERO DRIFT — safe to cut over")
- **feat(web)**: `dbparity serve` — a local web console (stdlib-only):
  launch comparison runs from the browser, live progress via polling, serving
  of HTML/JSON reports; localhost only, reports strictly inside the console's
  workdir
- **community**: CONTRIBUTING, SECURITY, issue forms (bug, feature,
  field report for real Oracle/MSSQL runs), a PR template
- **test(mssql)**: self-diagnosing asserts in the hash test (the cause is
  visible right in the CI message)
- **.gitignore**: run-time working files (checkpoints, incremental state,
  the console directory)

## 0.6.0 — 2026-07-03

Three independent work streams (developed in parallel).

- **feat(mssql)**: a full adapter — ODBC Driver 18 (dsn or
  host/port/…), a datetimeoffset converter, a digest API with T-SQL number
  canonicalization (compatible with trim_scale/TM9), binary collation; a live
  CI job on mcr.microsoft.com/mssql/server:2022 + env-gated integration tests
- **feat(core)**: incremental mode for dual-write — `incremental:
  {table: wm_column}`: after a full run, only changed rows are compared;
  missing/extra among them = drift; a state file carrying the config
  fingerprint, `--full` for a full re-comparison; incompatible modes
  (hash/resume) are resolved automatically with a note in the report
- **feat(report)**: the JSON report format is frozen — `schema_version: 1`,
  evolution rules, the docs/report-format.md and
  docs/config-reference.md references, a golden schema test in CI

## 0.5.0 — 2026-07-03

Three independent improvements (developed in parallel).

- **feat(adapters)**: binary collations for text PKs — `COLLATE BINARY`
  (sqlite) / `COLLATE "C"` (PG) / `NLSSORT BINARY` (Oracle) /
  `Latin1_General_BIN2` (MSSQL); merge order no longer depends on
  engine locales, the warning replaced by a guarantee
- **feat(cli)**: `dbparity validate -c config.yaml` — config checking without
  connecting to databases: type-specific required endpoint fields, value
  types, typo hints; `compare` now shows all config errors at once
- **infra**: a benchmark matrix in CI — `bench --json`, a workflow with
  performance regression thresholds and metrics in the summary

## 0.4.0 — 2026-07-03

Resilience: interruptions no longer wipe out a multi-hour comparison run.

- **feat**: checkpoint/resume — atomic JSON state with the config fingerprint;
  completed tables are restored without re-computation, the interrupted one
  resumes from its PK watermark (`checkpoint:` in the config, `--resume` in
  the CLI). A partial slot per table: a failed table does not lose its state
  because of its neighbors
- **feat**: retries on DB/network errors — `retry_attempts`/`retry_backoff_s`,
  a fresh pair of connections for every attempt, resumption from the
  watermark within the same run
- **feat**: open-ended `pk_range` (WHERE pk >= lo) in all adapters
- **refactor(engine)**: connections are issued per table (rather than per
  run) — cleaner retries, friendlier to poolers and single-threaded
  environments
- Parity with the reference result after an interruption is locked in by
  tests (a simulated network drop mid-stream, row-counter accuracy at the
  watermark boundary)

## 0.3.0 — 2026-07-02

Big tables: hash mode.

- **feat**: bucketed DB-side hashes in a single scan (`strategy: auto|stream|hash`,
  `hash_leaf_rows`) — matching PK buckets are credited via aggregates without
  transferring rows; diverged ones are drilled down with a streaming merge
  and full normalization. On the 300K×2 bench with 3 diffs, 60K rows are
  transferred instead of 600K
- **feat**: adapter digest API (sqlite: Python UDF md5; PG: md5+trim_scale;
  Oracle: STANDARD_HASH+TM9, experimental); cross-engine number
  canonicalization (`100.00` == `100` == `100.0`) verified by a live
  sqlite↔PostgreSQL test
- **feat**: a pk_range filter in stream_rows of all adapters
- **fix(postgres)**: the `prepare_threshold` option (shared-session
  environments: PGlite, poolers in transaction mode)
- Hash-vs-stream parity is locked in by tests; NULL PKs and empty tables
  are handled correctly in hash mode

## 0.2.0 — 2026-07-02

Correctness & Scale basics (see ROADMAP.md).

- **fix(oracle)**: NUMBER is now read as Decimal rather than float —
  silent precision loss eliminated; LOBs arrive as values
- **feat**: NULL in PK — a dedicated `null_pk` category instead of
  landing in the merge non-deterministically
- **feat**: a warning about text PK columns (collation differences
  between engines) in the report and the CLI
- **feat**: parallel table comparison — `workers: N` in the config /
  `--workers` in the CLI, one connection per thread
- **feat**: live comparison progress in the CLI
- **infra**: automatic PyPI publishing workflow on `v*` tags

## 0.1.0 — 2026-07-02

First public release (alpha).

- Core: streaming merge comparison by PK, O(n)/O(batch), a fast path
  keyed on logical column types (~400K rows/s on 1M rows)
- Normalization of "migration traps": Oracle `''`==NULL, trailing zeros,
  a float epsilon, time zones, CHAR padding, Unicode NFC, `0/1/Y/N`↔bool,
  BLOB→MD5, timestamp precision
- Schema comparison: columns, logical types, PKs (case-insensitive)
- Adapters: SQLite (+dialect emulation), PostgreSQL (psycopg3,
  server-side cursors; verified against a live PG 18), Oracle (thin,
  experimental — testers needed), MSSQL (skeleton)
- Reports: self-contained HTML (Tabler + Chart.js, dark theme,
  value masking) and JSON; a CLI with exit codes for CI/CD
- 34 automated tests, including live PostgreSQL integration
