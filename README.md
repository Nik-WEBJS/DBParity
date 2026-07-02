# DBParity

**Prove your database migration didn't lose or corrupt a single row.**

[Русская версия →](README.ru.md)

DBParity is a data equivalence verifier for database migrations
(Oracle / MSSQL / SQLite → PostgreSQL). It streams both databases in
primary-key order, compares them row by row with migration-aware
normalization, and produces a client-ready HTML report you can put on
the table at project sign-off.

> Status: **v0.1 alpha**. Core engine and PostgreSQL adapter are tested
> (including against a live PostgreSQL 18). The Oracle adapter is
> written but not yet battle-tested — **testers with real Oracle
> instances are very welcome**, please open an issue.

## Why

Migration projects rarely fail at code conversion — they fail at data
reconciliation. Gartner predicts >70% of mainframe exit projects will
fail, largely due to overestimating AI code conversion; Birmingham City
Council's ERP migration went from a £19M estimate to £144M+, hinging on
broken reconciliation. Plenty of tools convert schemas and SQL; very few
*prove the data arrived intact*.

## What it catches

Real diffs — lost rows, extra rows, changed values, duplicate PKs,
schema drift (missing columns, type changes, PK mismatch) — while **not**
flagging things that only look different:

| Migration trap | Handling |
|---|---|
| Oracle `''` == `NULL` (VARCHAR2) | normalized when source dialect is Oracle |
| `1.50` vs `1.5` (NUMBER→NUMERIC) | Decimal comparison |
| float noise | configurable epsilon |
| timezones | normalize to UTC |
| Oracle `DATE` carries time / PG `date` doesn't | optional midnight truncation |
| `CHAR(n)` space padding | optional rtrim |
| Unicode composition | NFC normalization |
| `0/1/'Y'/'N'` vs `boolean` | numeric mapping |
| BLOBs | MD5 comparison |
| timestamp precision (µs vs ns) | truncation to common precision |

## Quick start

```bash
pip install -e ".[postgres]"        # from a cloned repo
dbparity demo --outdir demo_out     # built-in demo with planted diffs
# → open demo_out/dbparity_report.html
dbparity compare -c config.yaml     # real comparison
```

### config.yaml

```yaml
source:
  type: oracle            # oracle | mssql | sqlite | postgres
  label: "Oracle PROD"
  user: app
  password: "..."
  dsn: "host:1521/ORCLPDB"
target:
  type: postgres
  label: "PostgreSQL NEW"
  dsn: "host=10.0.0.5 dbname=app user=app password=..."
tables: [customers, orders]          # default: all common tables
pk_overrides: {events: [id, ts]}     # when PK isn't declared in the DB
exclude_columns: {orders: [etl_ts]}  # service columns
rules:
  rtrim_strings: true
  float_epsilon: 1.0e-9
mask_values: false                   # true → hide values in the report
report:
  html: report.html
  json: report.json
```

### Exit codes

`0` — equivalent · `1` — differences found · `2` — run error.
Use it as a mandatory CI/CD gate before switching traffic.

## Performance

Streaming merge, O(n) time, O(batch) memory — the full table never
sits in RAM. `python3 bench/bench.py 1000000` (1M rows per side,
7 columns): **~400K rows/s** with the type-compiled fast path
(~310K generic). Next on the roadmap: DB-side segment hashing to skip
identical PK ranges entirely.

## Testing

```bash
pip install -e ".[dev,postgres]"
pytest tests/ -v
```

Live-PostgreSQL integration test (any of the three):

```bash
# Docker
docker compose up -d
DBPARITY_PG_DSN="host=127.0.0.1 port=5432 dbname=dbparity user=postgres password=dbparity" \
  pytest tests/test_postgres_integration.py -v

# or without Docker — PGlite (Postgres-in-WASM):
npm install @electric-sql/pglite @electric-sql/pglite-socket
node scripts/pglite_server.mjs &
DBPARITY_PG_DSN="host=127.0.0.1 port=5433 user=postgres dbname=postgres" \
  pytest tests/test_postgres_integration.py -v
```

CI runs the full suite on Python 3.10–3.12 plus the integration test
against PostgreSQL 16.

## Roadmap

DB-side segment hashing for 100M+ row tables · parallel-run mode ·
resume after interruption · Oracle/MSSQL integration hardening ·
web console on top of the engine.

Architecture notes (in Russian): [PLAN.md](PLAN.md).

## Contributing

The most valuable contribution right now is **a run against your real
Oracle/MSSQL → PostgreSQL migration** and an issue with what broke.
PRs welcome.

## License

[MIT](LICENSE)
