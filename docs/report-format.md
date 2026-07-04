# DBParity JSON report format — schema v1

Reference for the machine-readable report that DBParity writes to the
`report.json` path from the config (or via
`dbparity.report.render.write_json`). The report is a serialized
`RunResult.to_dict()` (`dbparity/core/models.py`).

- Current schema version: **1** (the `REPORT_SCHEMA_VERSION` constant
  in `dbparity/core/models.py`).
- The file is written in UTF-8, `ensure_ascii=False`, `indent=2`.
- The `schema_version` key always comes first — a consumer can check the
  version before parsing the rest of the document.
- The frozen key set is guarded by the golden test
  `tests/test_report_schema.py`.

## Compatibility guarantees

The format follows semver-like rules (roadmap v0.9):

| Change | Class | `schema_version` |
|---|---|---|
| Adding a new key (at any level) | minor | unchanged |
| Removing or renaming a key | major | incremented |
| Changing the type or semantics of an existing key's value | major | incremented |

Consequences for consumers:

1. **Ignore unknown keys.** New keys may appear in any release without a
   `schema_version` bump; every one of them must be documented in this
   document.
2. **Do not rely on key order**, beyond the "`schema_version` comes first"
   guarantee. The actual order is stable (dataclass field order),
   but it is not part of the contract.
3. **Check `schema_version`** and refuse to parse a report with a higher
   schema version than you support.
4. Keys documented below as v1 will not disappear or change type
   without a `schema_version` increment.

## Top level

| Key | Type | Semantics |
|---|---|---|
| `schema_version` | int | Report schema version. Always `1` in v1. |
| `source_label` | string | Human-readable source label: the endpoint's `label` from the config, or an auto-generated one (`sqlite:<path>` or the endpoint type). |
| `target_label` | string | The same for the target. |
| `started_at` | string | Run start time, UTC. `str(datetime)` format: `"YYYY-MM-DD HH:MM:SS.ffffff+00:00"`. |
| `finished_at` | string | Run finish time, same format. |
| `tables` | array | Results for the compared tables (see "tables[] element"). Also contains "error" entries: a table requested via the config's `tables` but missing on at least one side lands here with `error` populated. |
| `schema_diffs` | array | Schema differences — only tables that have any (see "schema_diffs[] element"). An empty array = the schemas of the common tables matched. |
| `tables_only_in_source` | array<string> | Names of tables present only in the source (in the source's original case), sorted. |
| `tables_only_in_target` | array<string> | Tables present only in the target. |
| `config_summary` | object | Snapshot of the run settings (see "config_summary"). Sensitive option values (`password`, `passwd`, `secret`, `token`) are masked with the string `"•••"`. |
| `equivalent` | bool | Final verdict: `true` ⇔ the schemas are clean (`schema_diffs` and `tables_only_in_*` are empty) **and** every table has `status == "ok"`. |
| `totals` | object | Aggregates across all tables (see "totals"). |

## `tables[]` element

One entry per table. Row counters are non-negative integers.

| Key | Type | Semantics |
|---|---|---|
| `table` | string | Table name (lower-cased — the shared "logical" case used by the comparison). |
| `pk` | array<string> | Primary key columns the merge ran on (lower case). An empty array — the PK could not be determined (see `error`). |
| `src_rows` | int | Rows accounted for on the source side (read via streaming + credited from hash segments). |
| `dst_rows` | int | Rows accounted for on the target side. |
| `matched` | int | Rows that matched completely (by normalized values). |
| `mismatched` | int | Rows with the same PK but different values in at least one column. |
| `missing_in_target` | int | Source rows absent from the target. |
| `extra_in_target` | int | Extra target rows (absent from the source). |
| `duplicate_pk` | int | Primary key duplicates (both sides combined). |
| `null_pk` | int | Rows with NULL in the PK — the merge cannot handle them, so they are counted as a separate difference category. |
| `samples` | array | Difference samples, at most `sample_limit` per table (see "samples[] element"). |
| `column_mismatch_counts` | object | `{column: N}` — the number of `mismatch` rows in which this column diverged. Only columns with N > 0. |
| `warnings` | array<string> | Human-readable warnings (do not affect `status`): text PK collations, "Restored from checkpoint — not re-compared", "hash mode unavailable (…) — used streaming comparison", and the like. |
| `error` | string \| null | Error text if the table could not be compared (no PK, PK outside the common columns, table missing, DB error after all retries). When `error != null` the counters are unreliable and `status == "error"`. |
| `duration_s` | float | Table comparison duration, seconds (rounded to 3 digits). |
| `mode` | string | Comparison mode: `"stream"` (streaming merge) or `"hash"` (segment-level DB-side aggregates + drill-down of diverged segments). |
| `rows_hash_matched` | int | Rows credited as equivalent via matching hash segments, with no data transferred. `0` in stream mode. |
| `rows_streamed` | int | Rows drilled down via streaming in hash mode (src+dst total across diverged segments). `0` in stream mode. |
| `segments_matched` | int | Hash segments that matched in full. `0` in stream mode. |
| `segments_streamed` | int | Hash segments sent to streaming drill-down. `0` in stream mode. |
| `total_diffs` | int | Sum of the differences: `mismatched + missing_in_target + extra_in_target + duplicate_pk + null_pk`. |
| `status` | string | `"ok"` (no differences), `"diff"` (differences found), `"error"` (comparison failed). |
| `match_pct` | float | `matched / max(src_rows, dst_rows) * 100`, rounded to 4 digits; `100.0` for an empty table. |

## `samples[]` element

One difference sample. The only object in schema v1 with a **strictly**
fixed key set (exactly three; the test checks for equality):

| Key | Type | Semantics |
|---|---|---|
| `kind` | string | The kind of difference, one of: `"missing_in_target"`, `"extra_in_target"`, `"mismatch"`, `"duplicate_pk"`, `"null_pk"`. |
| `pk` | array | The row's PK values in display form: values are stringified and truncated to 120 characters, `null` stays `null`. The PK is **not** masked even with `mask_values: true`. |
| `columns` | object \| null | Only for `kind == "mismatch"`: `{column: [source_value, target_value]}`. Values are display values (a string ≤ 120 characters, `null`, or `"•••"` with `mask_values: true`). For the other `kind` values — `null`. |

## `schema_diffs[]` element

| Key | Type | Semantics |
|---|---|---|
| `table` | string | Table name. |
| `missing_in_target` | array<string> | Columns absent from the target. |
| `extra_in_target` | array<string> | Extra target columns. |
| `type_changes` | array | Type changes: `{column, source, target}` objects with the column name and the engines' "raw" types. |
| `pk_mismatch` | object \| null | Primary key difference: `{source: [...], target: [...]}` or `null`. |

## `config_summary`

A snapshot of the run's key settings — for reproducibility and for display
in the report. The set may grow in minor releases (the "ignore unknown
keys" rule applies here too).

| Key | Type | Semantics |
|---|---|---|
| `source`, `target` | object | The endpoint's `{type, label, options}`; in `options`, values of sensitive keys are replaced with `"•••"`. |
| `rules` | object | All normalization rules (`NormalizeRules`, see `docs/config-reference.md`). |
| `sample_limit` | int | Sample limit per table. |
| `batch_size` | int | Read chunk size. |
| `mask_values` | bool | Value masking in samples. |
| `workers` | int | Number of parallel threads. |
| `strategy` | string | `auto` \| `stream` \| `hash`. |
| `retry_attempts` | int | Attempts per table. |
| `checkpoint` | bool | Whether checkpointing was enabled (the path itself is not disclosed). |

## `totals`

Aggregates over all `tables[]` elements (sums of the corresponding counters).

| Key | Type | Semantics |
|---|---|---|
| `tables_total` | int | Total number of compared tables (length of `tables`). |
| `tables_ok` | int | Tables with `status == "ok"`. |
| `src_rows`, `dst_rows` | int | Total source/target rows. |
| `matched`, `mismatched`, `missing_in_target`, `extra_in_target`, `duplicate_pk`, `null_pk` | int | Sums of the same-named per-table counters. |
| `total_diffs` | int | Sum of all differences. |
| `match_pct` | float | `matched / max(src_rows, dst_rows) * 100`, rounded to 4 digits; `100.0` when there are zero rows. |

## Example report

Actual output of a demo run (`dbparity demo`); `samples` are trimmed
to a couple of items per table.

```json
{
  "schema_version": 1,
  "source_label": "Oracle PROD (emulated)",
  "target_label": "PostgreSQL NEW",
  "started_at": "2026-07-02 21:19:57.637393+00:00",
  "finished_at": "2026-07-02 21:19:57.675259+00:00",
  "tables": [
    {
      "table": "customers",
      "pk": ["id"],
      "src_rows": 1200,
      "dst_rows": 1199,
      "matched": 1193,
      "mismatched": 4,
      "missing_in_target": 3,
      "extra_in_target": 2,
      "duplicate_pk": 0,
      "null_pk": 0,
      "samples": [
        {
          "kind": "mismatch",
          "pk": ["10"],
          "columns": {
            "name": ["Alexey Petrov", "Alexey Petrov (renamed)"]
          }
        },
        {
          "kind": "missing_in_target",
          "pk": ["101"],
          "columns": null
        }
      ],
      "column_mismatch_counts": {
        "name": 1,
        "balance": 1,
        "email": 1,
        "is_active": 1
      },
      "warnings": [],
      "error": null,
      "duration_s": 0.009,
      "mode": "stream",
      "rows_hash_matched": 0,
      "rows_streamed": 0,
      "segments_matched": 0,
      "segments_streamed": 0,
      "total_diffs": 9,
      "status": "diff",
      "match_pct": 99.4167
    },
    {
      "table": "orders",
      "pk": ["id"],
      "src_rows": 5000,
      "dst_rows": 5000,
      "matched": 4997,
      "mismatched": 3,
      "missing_in_target": 0,
      "extra_in_target": 0,
      "duplicate_pk": 0,
      "null_pk": 0,
      "samples": [
        {
          "kind": "mismatch",
          "pk": ["500"],
          "columns": {"amount": ["1890.0", "1890.02"]}
        }
      ],
      "column_mismatch_counts": {"amount": 2, "status": 1},
      "warnings": [],
      "error": null,
      "duration_s": 0.027,
      "mode": "stream",
      "rows_hash_matched": 0,
      "rows_streamed": 0,
      "segments_matched": 0,
      "segments_streamed": 0,
      "total_diffs": 3,
      "status": "diff",
      "match_pct": 99.94
    },
    {
      "table": "products",
      "pk": ["id"],
      "src_rows": 300,
      "dst_rows": 300,
      "matched": 300,
      "mismatched": 0,
      "missing_in_target": 0,
      "extra_in_target": 0,
      "duplicate_pk": 0,
      "null_pk": 0,
      "samples": [],
      "column_mismatch_counts": {},
      "warnings": [],
      "error": null,
      "duration_s": 0.001,
      "mode": "stream",
      "rows_hash_matched": 0,
      "rows_streamed": 0,
      "segments_matched": 0,
      "segments_streamed": 0,
      "total_diffs": 0,
      "status": "ok",
      "match_pct": 100.0
    }
  ],
  "schema_diffs": [
    {
      "table": "orders",
      "missing_in_target": ["discount"],
      "extra_in_target": [],
      "type_changes": [],
      "pk_mismatch": null
    }
  ],
  "tables_only_in_source": ["legacy_log"],
  "tables_only_in_target": ["audit_new"],
  "config_summary": {
    "source": {
      "type": "sqlite",
      "label": "Oracle PROD (emulated)",
      "options": {
        "path": "/tmp/dbparity_demo/source_oracle_like.db",
        "dialect_emulation": "oracle"
      }
    },
    "target": {
      "type": "sqlite",
      "label": "PostgreSQL NEW",
      "options": {"path": "/tmp/dbparity_demo/target_postgres_like.db"}
    },
    "rules": {
      "oracle_empty_string_is_null": true,
      "rtrim_strings": true,
      "unicode_nfc": true,
      "float_epsilon": 1e-09,
      "yn_as_bool": false,
      "truncate_time_if_midnight": false,
      "timestamp_precision": 6,
      "tz_to_utc": true,
      "bytes_as_md5": true
    },
    "sample_limit": 50,
    "batch_size": 5000,
    "mask_values": false,
    "workers": 1,
    "strategy": "auto",
    "retry_attempts": 1,
    "checkpoint": false
  },
  "equivalent": false,
  "totals": {
    "tables_total": 3,
    "tables_ok": 1,
    "src_rows": 6500,
    "dst_rows": 6499,
    "matched": 6490,
    "mismatched": 7,
    "missing_in_target": 3,
    "extra_in_target": 2,
    "duplicate_pk": 0,
    "null_pk": 0,
    "total_diffs": 12,
    "match_pct": 99.8462
  }
}
```

## Schema version history

| `schema_version` | Changes |
|---|---|
| 1 | First frozen version (DBParity 0.5.x). |
