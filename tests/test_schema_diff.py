"""Unit tests for the schema comparison."""
from dbparity.adapters.base import ColumnSchema, TableSchema
from dbparity.core.schema_diff import diff_schemas, diff_table_schema


def ts(name, cols, pk):
    return TableSchema(
        name=name,
        columns=[ColumnSchema(n, logical, raw=logical) for n, logical in cols],
        pk=pk,
    )


def test_clean_schema():
    a = ts("t", [("id", "number"), ("name", "text")], ["id"])
    b = ts("t", [("id", "number"), ("name", "text")], ["id"])
    assert not diff_table_schema("t", a, b).has_diffs
    assert diff_schemas({"t": a}, {"t": b}) == []


def test_case_insensitive_matching():
    # Oracle returns UPPER, Postgres lower: this is not a diff
    a = ts("t", [("ID", "number"), ("NAME", "text")], ["ID"])
    b = ts("t", [("id", "number"), ("name", "text")], ["id"])
    assert not diff_table_schema("t", a, b).has_diffs


def test_missing_and_extra_columns():
    a = ts("t", [("id", "number"), ("discount", "float")], ["id"])
    b = ts("t", [("id", "number"), ("audit_ts", "datetime")], ["id"])
    d = diff_table_schema("t", a, b)
    assert d.missing_in_target == ["discount"]
    assert d.extra_in_target == ["audit_ts"]


def test_type_change():
    a = ts("t", [("id", "number"), ("price", "number")], ["id"])
    b = ts("t", [("id", "number"), ("price", "text")], ["id"])
    d = diff_table_schema("t", a, b)
    assert len(d.type_changes) == 1
    assert d.type_changes[0]["column"] == "price"


def test_pk_mismatch():
    a = ts("t", [("id", "number"), ("ts", "datetime")], ["id"])
    b = ts("t", [("id", "number"), ("ts", "datetime")], ["id", "ts"])
    d = diff_table_schema("t", a, b)
    assert d.pk_mismatch == {"source": ["id"], "target": ["id", "ts"]}
