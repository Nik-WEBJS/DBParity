"""Golden test for the JSON report format (schema v1, docs/report-format.md).

Catches accidental changes to the frozen format in CI:

- REMOVING or RENAMING a key from a frozen set fails the test:
  that is a major change requiring a REPORT_SCHEMA_VERSION bump
  and an update to docs/report-format.md.
- ADDING new keys is deliberately ALLOWED (the check is "frozen set
  is a subset of the actual one", not strict equality) - that is a
  minor change. NOTE: every new key must be documented in
  docs/report-format.md - the test cannot verify that.
- Strict key-set equality applies only to samples[] elements
  (the v1 contract fixes exactly {kind, pk, columns}).
"""
import json

import pytest

from dbparity.core import engine
from dbparity.core.models import REPORT_SCHEMA_VERSION
from dbparity.demo.seed import build_demo
from dbparity.report.render import render_html, write_json

# --- frozen key sets of schema v1 (see docs/report-format.md) ----------------

FROZEN_TOP_LEVEL = {
    "schema_version",
    "source_label",
    "target_label",
    "started_at",
    "finished_at",
    "tables",
    "schema_diffs",
    "tables_only_in_source",
    "tables_only_in_target",
    "config_summary",
    "equivalent",
    "totals",
}

FROZEN_TABLE_KEYS = {
    "table",
    "pk",
    "src_rows",
    "dst_rows",
    "matched",
    "mismatched",
    "missing_in_target",
    "extra_in_target",
    "duplicate_pk",
    "null_pk",
    "samples",
    "column_mismatch_counts",
    "warnings",
    "error",
    "duration_s",
    "mode",
    "rows_hash_matched",
    "rows_streamed",
    "segments_matched",
    "segments_streamed",
    "total_diffs",
    "status",
    "match_pct",
}

# The only strict contract: a sample has EXACTLY these three keys
SAMPLE_KEYS = {"kind", "pk", "columns"}

KNOWN_KINDS = {
    "missing_in_target",
    "extra_in_target",
    "mismatch",
    "duplicate_pk",
    "null_pk",
}

FROZEN_TOTALS_KEYS = {
    "tables_total",
    "tables_ok",
    "src_rows",
    "dst_rows",
    "matched",
    "mismatched",
    "missing_in_target",
    "extra_in_target",
    "duplicate_pk",
    "null_pk",
    "total_diffs",
    "match_pct",
}

FROZEN_SCHEMA_DIFF_KEYS = {
    "table",
    "missing_in_target",
    "extra_in_target",
    "type_changes",
    "pk_mismatch",
}


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """One demo run per module: RunResult + the JSON re-read from disk.

    We verify the written write_json artifact specifically - that is the
    frozen interchange format, not an intermediate Python dict.
    """
    tmp = tmp_path_factory.mktemp("report_schema")
    run = engine.run(build_demo(tmp))
    path = write_json(run, tmp / "report.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return run, data


def _missing(frozen: set, actual: dict, where: str) -> str:
    lost = frozen - set(actual)
    return (f"{where}: keys {sorted(lost)} disappeared from the frozen v1 "
            f"schema - this is a major change: bump REPORT_SCHEMA_VERSION "
            f"and update docs/report-format.md")


def test_schema_version_frozen(report):
    _, data = report
    assert data["schema_version"] == REPORT_SCHEMA_VERSION
    assert data["schema_version"] == 1, (
        "The schema version changed - update this test's frozen sets "
        "and docs/report-format.md")
    # schema_version is the first meaningful field of the report
    assert next(iter(data)) == "schema_version"


def test_top_level_keys_frozen(report):
    _, data = report
    # Not strict equality: NEW keys are allowed (a minor change),
    # but they must be documented in docs/report-format.md.
    assert FROZEN_TOP_LEVEL <= set(data), _missing(
        FROZEN_TOP_LEVEL, data, "top level")


def test_table_keys_frozen(report):
    _, data = report
    assert data["tables"], "the demo run must return tables"
    # tables[0] is the representative; also check every element:
    # all table records share the same key set.
    first_keys = set(data["tables"][0])
    assert FROZEN_TABLE_KEYS <= first_keys, _missing(
        FROZEN_TABLE_KEYS, data["tables"][0], "tables[0]")
    for t in data["tables"]:
        # new keys are allowed (see the module docstring), losses are not
        assert FROZEN_TABLE_KEYS <= set(t), _missing(
            FROZEN_TABLE_KEYS, t, f"tables[{t.get('table')!r}]")
        assert set(t) == first_keys, "table key sets are not uniform"


def test_sample_keys_strict(report):
    _, data = report
    samples = [s for t in data["tables"] for s in t["samples"]]
    assert samples, "the demo run must yield diff samples"
    for s in samples:
        # STRICT equality here: the v1 contract fixes exactly three keys.
        # Adding a key to a sample is a deliberate format change:
        # update docs/report-format.md and this test.
        assert set(s) == SAMPLE_KEYS, f"sample keys changed: {sorted(s)}"
        assert s["kind"] in KNOWN_KINDS, f"unknown kind: {s['kind']!r}"
        if s["kind"] == "mismatch":
            assert isinstance(s["columns"], dict) and s["columns"]
        else:
            assert s["columns"] is None
    # the demo covers the main diff kinds - the check is not vacuous
    kinds = {s["kind"] for s in samples}
    assert {"mismatch", "missing_in_target", "extra_in_target"} <= kinds


def test_totals_and_schema_diff_keys_frozen(report):
    _, data = report
    assert FROZEN_TOTALS_KEYS <= set(data["totals"]), _missing(
        FROZEN_TOTALS_KEYS, data["totals"], "totals")
    assert data["schema_diffs"], "the demo contains a schema diff (orders)"
    for sd in data["schema_diffs"]:
        assert FROZEN_SCHEMA_DIFF_KEYS <= set(sd), _missing(
            FROZEN_SCHEMA_DIFF_KEYS, sd, "schema_diffs[]")
    # the config_summary core is stable; it may grow via minor changes
    assert {"source", "target", "rules"} <= set(data["config_summary"])


def test_html_footer_shows_schema_version(report):
    run, _ = report
    assert f"report schema v{REPORT_SCHEMA_VERSION}" in render_html(run)
