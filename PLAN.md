# DBParity — MVP development plan

> Internal architecture / design document. For user-facing documentation see
> README.md, `docs/config-reference.md` and `docs/report-format.md`.

**Product:** a database migration verification tool — proves that the data in the
source (Oracle/MSSQL) and the target (PostgreSQL) are equivalent, and generates a report for the customer.
**Buyer:** system integrators running migration projects (CIS: critical-infrastructure regulation / import substitution → EU: Oracle exit, SAP 2027).

## 1. Technology and tooling

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.10+ | development speed, mature DB drivers, fast enough for streaming |
| Drivers | `oracledb` (thin, no Instant Client), `psycopg3`, `pyodbc` (MSSQL, optional), `sqlite3` (built in — demo/tests) | all optional: the core does not depend on the drivers |
| Config | YAML (`pyyaml`) | human-readable comparison scenarios |
| Report | Jinja2 → a single self-contained HTML file | the integrator forwards it to the customer as one file |
| Report UI kit | **Tabler** (Bootstrap 5, MIT, 40k+ stars) via CDN + Chart.js | the best open-source kit for data-heavy dashboards in 2026 |
| CLI | argparse + `rich` | zero framework tax, good-looking console output |
| Tests | `pytest` | the standard |

## 2. Code architecture

Hexagonal: a dialect-independent core with thin adapters at the edges.

```
dbparity/
├── cli.py                 # entry points: compare, demo
├── config.py              # YAML config loading/validation
├── adapters/
│   ├── base.py            # the Adapter interface: schema(), stream_rows(), row_count()
│   ├── sqlite_adapter.py  # demo and tests
│   ├── postgres_adapter.py# psycopg3, keyset pagination
│   ├── oracle_adapter.py  # python-oracledb thin mode
│   └── mssql_adapter.py   # pyodbc (optional stub)
├── core/
│   ├── models.py          # dataclasses: TableResult, RowDiff, RunResult…
│   ├── normalize.py       # type equivalence rules (see §3)
│   ├── compare.py         # streaming merge-diff of two PK-ordered streams
│   └── schema_diff.py     # structure comparison: tables, columns, types, PKs
├── report/
│   ├── render.py          # Jinja2 → HTML
│   └── templates/report.html.j2
├── demo/seed.py           # two demo databases with pre-planted differences
└── tests/                 # unit + integration
```

**Data flow:** `config.yaml → [Adapter src] + [Adapter dst] → schema_diff →
for each table: two row streams ordered by PK → normalize →
merge-diff → TableResult → RunResult → render (HTML + JSON) + exit code`.

**Comparison algorithm** — a merge of two PK-ordered streams, O(n) in time,
O(batch) in memory (the server hands out rows in chunks, keyset pagination, no OFFSET):
- `pk_src < pk_dst` → the row was lost in the target (`missing_in_target`)
- `pk_src > pk_dst` → an extra row in the target (`extra_in_target`)
- equal → column-by-column comparison of the normalized values → `mismatch` with per-column detail

Difference samples are stored with a limit (50 per category by default),
while the counters are complete. Report values can be masked (`mask_values: true`).

## 3. Normalization rules (the core of the value proposition — "migration traps")

| Trap | Rule |
|---|---|
| Oracle: `''` == `NULL` in VARCHAR2 | with source=oracle, an empty string → NULL |
| `NUMBER` vs `NUMERIC`: `1.50` vs `1.5` | comparison via Decimal, trailing-zero normalization |
| Floats | comparison with a configurable epsilon |
| Oracle `DATE` carries a time component | truncate_time_if_date_target option |
| Time zones | conversion to UTC before comparison |
| `CHAR(n)` space padding | rtrim option |
| Unicode | NFC normalization |
| Booleans: `0/1/'Y'/'N'` vs `bool` | a mapping table |
| BLOB/bytea | comparison by MD5 |
| Timestamp precision (µs vs ns) | truncation to the common precision |

## 4. Presentation layer (step 2 of the assignment)

The report is the product's storefront: a verdict banner (EQUIVALENT / NOT EQUIVALENT + match percentage),
KPI cards (rows checked, differences, tables OK), a donut chart of the overall result,
a bar chart of differences per table, schema differences, and expandable difference
samples with the diverging columns highlighted. Styling — Tabler via CDN, dark/light theme.
Plus tidy console output (rich): a results table + the verdict.

## 5. Testing (step 3 of the assignment)

- Unit: normalize (every trap), compare (all difference categories: missing/extra/mismatch/PK duplicates), schema_diff, config.
- Integration: an end-to-end demo run on sqlite pairs → the expected difference counters, valid HTML, the correct exit code (0 — equivalent, 1 — differences).
- Sandbox limitation: Oracle/MSSQL get no integration testing (no servers available) — the adapters are covered by contract tests through the shared interface; the sqlite adapter emulates the dialect flags.

## 6. MVP limitations and roadmap

The MVP deliberately leaves out: parallel-run, test generation from traffic, comparison
of performance plans, a distributed mode. Roadmap: segmented DB-side hashes
(comparing aggregate hashes over PK ranges, drilling down only on divergence) →
parallel-run → SAP→1C connectors → a web console on top of the core.

## 7. Hardening status (done after the MVP)

- **Live PostgreSQL integration**: the psycopg adapter has been verified against a live
  PostgreSQL 18 over the wire protocol (in the sandbox — PGlite + pglite-socket; locally — docker compose).
  Introspection, server-side cursors, numeric/boolean/timestamptz/date — no false
  positives on the cross-engine traps; the `server_side: false` option for environments
  without DECLARE CURSOR.
- **Normalization fast path**: per-column precompiled functions keyed on the schema's
  logical types (bypassing the isinstance chain), with a fallback to the generic path on an
  unexpected type. Parity with the generic path is locked in by a test. Benchmark on 1M rows
  × 2 sides: 310 → 400K rows/s (×1.3), `bench/bench.py`.
