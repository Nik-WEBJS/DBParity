# Contributing to DBParity

Thanks for your interest! DBParity is a *proof* tool: people sign off
multi-month migrations based on its output. That single fact shapes every
guideline below — when in doubt, optimize for correctness and verifiability.

## The most valuable contribution right now

**A run against your real Oracle / MSSQL → PostgreSQL migration.**
The core engine and the PostgreSQL adapter are exercised by CI against live
databases; the Oracle adapter is written but not yet battle-tested, and MSSQL
has seen far fewer real datasets than synthetic ones. If you have access to a
real migration — even a staging copy — please run DBParity on it and file a
[**Field report: real migration run**](https://github.com/Nik-WEBJS/DBParity/issues/new?template=oracle_mssql_field_report.yml)
issue. "Everything matched" is just as valuable as a failure story: v1.0 is
gated on real migrations verified by the community (see [ROADMAP.md](ROADMAP.md)).

## Development setup

```bash
git clone https://github.com/Nik-WEBJS/DBParity && cd DBParity
pip install -e ".[dev,postgres]"      # add [oracle] / [mssql] if you work on those adapters
```

Python 3.10+ is required. Runtime dependencies are deliberately minimal
(`pyyaml`, `jinja2`, `rich`); database drivers are optional extras. Please
keep it that way — prefer the standard library over new dependencies.

## Running tests

The unit/engine suite needs **no database** (it runs on in-process SQLite):

```bash
pytest tests/ -v
```

**Live PostgreSQL** integration tests, via Docker:

```bash
docker compose up -d
DBPARITY_PG_DSN="host=127.0.0.1 port=5432 dbname=dbparity user=postgres password=dbparity" \
  pytest tests/test_postgres_integration.py -v
```

No Docker? Use **PGlite** (a real Postgres compiled to WASM):

```bash
npm install @electric-sql/pglite @electric-sql/pglite-socket
node scripts/pglite_server.mjs &
DBPARITY_PG_DSN="host=127.0.0.1 port=5433 user=postgres dbname=postgres" pytest -v
```

**MSSQL**: `tests/test_mssql_integration.py` needs a SQL Server instance plus
the `msodbcsql18` ODBC driver, which is fiddly to set up locally. The easy
path is to rely on CI — every pull request runs a live
`mssql-server:2022` job, the full test matrix on Python 3.10–3.12, live
PostgreSQL 16, and the benchmark workflow.

**Oracle**: there is no Oracle instance in CI. If you have a real one, that's
exactly the field-report contribution described above.

## Code style

- **English is the project's primary language.** DBParity grew out of
  Oracle/MSSQL → PostgreSQL (including Postgres Pro) migrations in the
  Russian-speaking market, and its early docstrings and comments were written
  in Russian; ahead of the international launch, the codebase and the docs
  were translated to English. Everything *user-facing* — CLI output, config
  keys, report contents, `docs/`, README (EN, with a Russian mirror in
  `README.ru.md`) — is English. Please write new docstrings and comments in
  English.
- **Conventional Commits** for commit messages and PR titles:
  `feat:`, `fix:`, `docs:`, `test:`, `perf:`, `refactor:`, `chore:`
  (scopes welcome, e.g. `feat(oracle): ...`).
- Match the surrounding code; no new mandatory dependencies without prior
  discussion in an issue.

## Pull request process

1. **Open an issue first** for anything non-trivial, and check
   [ROADMAP.md](ROADMAP.md) — your idea may already be planned (or
   deliberately deferred).
2. **Tests are mandatory.** Every behavior change needs a test; every bug fix
   needs a regression test that fails without the fix.
3. **The benchmark must not regress.** `.github/workflows/bench.yml` runs on
   every PR and fails it if the compare core drops below the thresholds
   (fast-path / generic rows-per-second floors, and the planted-diff counters
   must stay exact). If you touch the hot path (compare loop, normalizers,
   hash mode), run `python3 bench/bench.py 200000` locally before pushing.
4. **Document new config keys** in `docs/config-reference.md`. Changes to the
   JSON report must respect the frozen format (`schema_version: 1`): the
   golden test `tests/test_report_schema.py` must pass, and evolution rules
   live in `docs/report-format.md`.
5. CI (tests on 3.10–3.12, live PostgreSQL and MSSQL jobs, bench) must be
   green before review.

## Architecture principles (the short version)

- **Correctness > scale > convenience.** A false "EQUIVALENT" is the worst
  bug this project can have — someone will sign a cutover based on it. When
  in doubt, report a difference rather than silently equalize values.
- **Fast paths may only degrade toward *more* verification, never less.** An
  imperfect canonical mapping in hash mode causes extra drill-down (slower),
  **never a false skip**: every diverged hash bucket is re-verified by the
  row-level engine with full normalization. Any optimization you add must
  preserve this property.
- **The verifier must not lose precision itself**: Oracle `NUMBER` is fetched
  as `Decimal` (never float), BLOBs are compared as digests rather than
  truncated.
- Each normalization rule neutralizes exactly one documented *migration
  trap* — a difference of representation, never a difference of content.
- Exit codes (`0/1/2`) and the JSON report schema are public API; breaking
  them is a major-version event.

Fuller architecture notes live in [PLAN.md](PLAN.md).
