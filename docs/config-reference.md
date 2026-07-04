# config.yaml reference

The complete list of DBParity configuration keys as implemented in
`dbparity/config.py` (DBParity 0.5.x). The config is a YAML mapping, loaded
with `dbparity run -c config.yaml`. Before the config object is built, the
mapping goes through validation (`validate_config_dict`, also exposed as the
`dbparity validate` command): an unknown top-level or rule key is an error
with a "did you mean …" hint.

## Top-level keys

| Key | Type | Default | Description |
|---|---|---|---|
| `source` | object | — (required) | Connection to the source (the reference, the "before" side). See "Endpoints". |
| `target` | object | — (required) | Connection to the target (the database under verification, the "after" side). |
| `tables` | list<string> \| null | `null` | Explicit list of tables to compare. `null` — all tables common to both databases. A listed table missing on either side lands in the report with `error` set. |
| `pk_overrides` | object | `{}` | `{table: [columns]}` — force a primary key (when the database has no PK or the existing one is unsuitable). Names are lower-cased. |
| `exclude_columns` | object | `{}` | `{table: [columns]}` — exclude columns from the comparison (audit fields, `updated_at`, etc.). Names are lower-cased. PK columns cannot be excluded — the table comparison would end with an error. |
| `rules` | object | all defaults | Value normalization rules. See "rules". |
| `strategy` | string | `auto` | `auto` \| `stream` \| `hash`. `stream` — streaming merge only; `hash` — request hash mode (an unsuitable table falls back to streaming + a warning); `auto` — hash mode wherever it applies. |
| `hash_leaf_rows` | int ≥ 1 | `20000` | PK bucket step in hash mode (≈ rows per segment with a dense key). Smaller — more precise localization of differences, more DB-side groupings. |
| `workers` | int ≥ 1 | `1` | Parallel table comparison (each thread opens its own connections to both databases). |
| `sample_limit` | int ≥ 0 | `50` | Maximum number of difference samples (`samples`) per table in the report. `0` — counters only. |
| `batch_size` | int ≥ 1 | `5000` | Chunk size for streaming row reads (fetchmany/arraysize/itersize). |
| `mask_values` | bool | `false` | Mask column values in difference samples with the string `•••` (PII). PK values are not masked. |
| `checkpoint` | string \| null | `null` | Path to the checkpoint file. When set, progress is saved periodically and `--resume` continues from the point of interruption. On resume without an explicit path, the auto-generated name `.dbparity_ckpt_<fingerprint>.json` is used. |
| `checkpoint_every_rows` | int ≥ 1000 | `500000` | How often (in processed src+dst rows) to take a checkpoint within a table. |
| `retry_attempts` | int ≥ 1 | `1` | Comparison attempts per table on DB/network errors. `1` = no retries. |
| `retry_backoff_s` | number ≥ 0 | `2.0` | Base pause between attempts; the actual pause is `retry_backoff_s × attempt_number`. |
| `incremental` | dict table→column | `{}` | Incremental mode for dual-write: one watermark column per table (present in both databases, growing monotonically whenever the row changes — a timestamp or a numeric version). After the first full run, only rows with `wm >= the saved maximum` are compared; missing/extra among the changed rows = drift. State lives in `.dbparity_incr_<fp>.json`; the `--full` flag forces a full re-comparison. Cannot be combined with hash mode or resume within a single run (resolved automatically, with a note in the report). |
| `report` | object | `{}` | Where to write reports: keys `html` and/or `json` (path strings). If unset, no report files are written. |

### `report`

| Key | Type | Default | Description |
|---|---|---|---|
| `report.html` | string \| null | `null` | Path for the self-contained HTML report. |
| `report.json` | string \| null | `null` | Path for the JSON report (frozen format — see `docs/report-format.md`). |

## Endpoints (`source` / `target`)

Each section is a mapping with a mandatory `type` plus connection parameters.
Every key except `type` and `label` is passed to the adapter as an option.

Common keys:

| Key | Type | Default | Description |
|---|---|---|---|
| `type` | string | — (required) | `sqlite` \| `postgres` (alias `postgresql`) \| `oracle` \| `mssql`. |
| `label` | string \| null | `null` | Human-readable label for reports (`source_label`/`target_label`). If unset, generated from the type/path. |

In the report's `config_summary`, option values whose names are `password`,
`passwd`, `secret`, `token` are masked (`•••`).

### type: sqlite

| Key | Type | Default | Description |
|---|---|---|---|
| `path` | string | — (required) | Path to the database file. |
| `dialect_emulation` | string \| null | `null` | Apply another engine's dialect-specific normalization rules to the data on this side. Example: `oracle` — enables `oracle_empty_string_is_null` on this side. Used by the demo and the tests; handy for "Oracle-like" dumps loaded into sqlite. |

### type: postgres | postgresql

Connect either with a ready-made `dsn` or with a `host`+`dbname`+`user` set
(the validator requires one of the two).

| Key | Type | Default | Description |
|---|---|---|---|
| `dsn` | string \| null | `null` | psycopg connection string (URI or keyword format). When set, the other connection parameters are not needed. |
| `host` | string | `localhost` | Host (when the DSN is assembled from parts). |
| `port` | int | `5432` | Port. |
| `dbname` | string | — | Database name (required without `dsn`). Alias: `database`. |
| `database` | string | — | Alias for `dbname`. |
| `user` | string | — | User (required without `dsn`). |
| `password` | string \| null | `null` | Password (masked in the report). |
| `schema` | string | `public` | Schema in which tables are looked up. |
| `server_side` | bool | `true` | Server-side (named) cursor for streaming reads. `false` — a regular cursor with `fetchmany` (for environments without `DECLARE CURSOR`, e.g. PGlite). |
| `prepare_threshold` | int \| null | unset | Passed through to `psycopg.Connection.prepare_threshold`. `null` disables auto-prepare — needed for shared sessions (PGlite, poolers in transaction mode). If the key is omitted, the psycopg default applies. |

### type: oracle

python-oracledb adapter (thin mode, no Instant Client). The adapter itself
enables `fetch_decimals` (NUMBER → Decimal, no precision loss)
and `fetch_lobs=False` (LOBs arrive as values).

| Key | Type | Default | Description |
|---|---|---|---|
| `user` | string | — (required) | User. |
| `password` | string | — (required) | Password (masked in the report). |
| `dsn` | string | — (required) | DSN of the form `host:port/service`. |
| `schema` | string | = `user` | Table owner; upper-cased. |

### type: mssql

pyodbc adapter: requires a system ODBC driver (msodbcsql18).

| Key | Type | Default | Description |
|---|---|---|---|
| `dsn` | string | — (required) | ODBC connection string (`Driver=...;Server=...;Database=...;UID=...;PWD=...`). |
| `schema` | string | `dbo` | Table schema. |

## `rules` — normalization rules

Each rule neutralizes one specific "migration trap": a difference in how the
data is represented that is NOT a difference in content. An unknown rule is
a validation error with a hint.

| Key | Type | Default | Description |
|---|---|---|---|
| `oracle_empty_string_is_null` | bool | `true` | **Trap:** Oracle physically stores `''` as NULL, while the target (PG and others) distinguishes the two — thousands of false `mismatch` results on text columns. The rule equates `''` with NULL **only on the side with the oracle dialect** (the oracle adapter, or sqlite with `dialect_emulation: oracle`). |
| `rtrim_strings` | bool | `false` | **Trap:** `CHAR(n)` in Oracle/MSSQL is space-padded to length n; after migrating to `VARCHAR` the padding is gone — `'abc   ' != 'abc'`. The rule strips trailing spaces (spaces only, not all whitespace) on both sides. |
| `unicode_nfc` | bool | `true` | **Trap:** the same visible text in different Unicode forms: `é` as the single code point U+00E9 or as `e`+U+0301 (composed vs decomposed). The rule normalizes strings to NFC on both sides. |
| `float_epsilon` | number ≥ 0 | `1e-9` | **Trap:** binary floats round-tripped through text or another engine diverge in the last bits (`0.30000000000000004`). Float values are rounded to `-log10(ε)` decimal digits before comparison. `0` — disable rounding (compare exact values). |
| `yn_as_bool` | bool | `false` | **Trap:** legacy `CHAR(1)` flags `'Y'/'N'/'T'/'F'` in the source vs a real `boolean` in the target. The rule maps these letters (case-insensitively) to 1/0, and boolean values to 1/0 as well. Careful: genuine textual values `'y'`, `'n'`, `'t'`, `'f'` in the data get converted too. |
| `truncate_time_if_midnight` | bool | `false` | **Trap:** Oracle `DATE` always carries a time component; migrating to a plain `date` drops it — `2025-01-01 00:00:00 != 2025-01-01`. Rule: a datetime whose time is exactly 00:00 is compared as a date. Careful: a genuine midnight timestamp becomes a date too. |
| `timestamp_precision` | int 0..6 | `6` | **Trap:** differing fractional-second precision (Oracle `TIMESTAMP(6)` vs MSSQL `datetime` with ~3 digits, and so on). Microseconds are truncated to N decimal digits on both sides. |
| `tz_to_utc` | bool | `true` | **Trap:** the same instant stored in different time zones (`12:00+03:00` and `09:00+00:00`) is not a difference. Aware datetimes are converted to UTC and compared without tzinfo (naive ones are left as is). |
| `bytes_as_md5` | bool | `true` | **Trap/safeguard:** BLOBs can be enormous. Byte values are compared as an `"md5:<hex>"` digest — only the hash reaches memory and the samples. `false` — compare raw bytes. |

## Full config example

```yaml
source:
  type: oracle
  label: Oracle PROD
  user: app
  password: "secret"          # masked in the report
  dsn: ora-host:1521/ORCLPDB1
  schema: APP

target:
  type: postgres
  label: PostgreSQL NEW
  host: pg-host
  port: 5432
  dbname: app
  user: verifier
  password: "secret"
  schema: public
  server_side: true
  # prepare_threshold: null   # for poolers/PGlite

tables: [customers, orders, products]

pk_overrides:
  orders_log: [order_id, seq]   # table with no PK in the database

exclude_columns:
  customers: [updated_at, sync_hash]

rules:
  rtrim_strings: true
  timestamp_precision: 3
  float_epsilon: 1.0e-6

strategy: auto
hash_leaf_rows: 20000
workers: 4
sample_limit: 50
batch_size: 5000
mask_values: false

checkpoint: ./ckpt.json
checkpoint_every_rows: 500000
retry_attempts: 3
retry_backoff_s: 2.0

report:
  html: ./out/report.html
  json: ./out/report.json
```

## Validation: constraint summary

- `source`/`target` are required; `type` must be one of `sqlite | postgres |
  postgresql | oracle | mssql`; type-specific required parameters (see above).
- Minimums: `workers ≥ 1`, `sample_limit ≥ 0`, `batch_size ≥ 1`,
  `hash_leaf_rows ≥ 1`, `checkpoint_every_rows ≥ 1000`,
  `retry_attempts ≥ 1`, `retry_backoff_s ≥ 0`.
- `strategy` — only `auto | stream | hash`.
- `tables` — a list of strings; `pk_overrides`/`exclude_columns` — mappings
  `{table: [columns]}`.
- `rules.timestamp_precision` — an integer 0..6; `rules.float_epsilon` —
  a number ≥ 0; the remaining rules are booleans.
- An unknown top-level or rule key is an error with a hint.

> Note: this reference reflects `config.py` as of DBParity 0.5.x;
> new keys are documented as they appear.
