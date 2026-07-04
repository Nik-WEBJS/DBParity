"""Binary collation for text PKs: consistent ordering for the merge comparison."""
import sqlite3

import pytest

from dbparity.adapters.sqlite_adapter import SQLiteAdapter
from dbparity.config import Config, EndpointConfig
from dbparity.core import engine

# Mixed-case and non-ASCII PKs: locale-aware collations order them
# differently, binary (UTF-8 bytes) ordering is always the same
_CODES = ["a", "B", "é", "z", "ü"]
_BINARY_ORDER = ["B", "a", "z", "é", "ü"]   # 0x42 < 0x61 < 0x7A < C3A9 < C3BC


def _make_pair(tmp_path):
    """Two identical sqlite DBs with a TEXT PK -> Config for engine.run."""
    for name in ("s.db", "d.db"):
        c = sqlite3.connect(tmp_path / name)
        c.execute("CREATE TABLE t (code TEXT PRIMARY KEY, v TEXT)")
        c.executemany("INSERT INTO t VALUES (?, ?)",
                      [(code, f"val {code}") for code in _CODES])
        c.commit()
        c.close()
    return Config(
        source=EndpointConfig("sqlite", None, {"path": str(tmp_path / "s.db")}),
        target=EndpointConfig("sqlite", None, {"path": str(tmp_path / "d.db")}),
    )


def test_text_pk_binary_sort_no_false_diffs(tmp_path):
    """Text PK: binary sort is consistent on both sides -> zero diffs."""
    run = engine.run(_make_pair(tmp_path))
    t = run.tables[0]
    assert t.error is None
    assert t.matched == len(_CODES)
    assert t.total_diffs == 0
    assert run.equivalent
    assert t.warnings and "binary collation applied" in t.warnings[0]


def test_unsupported_adapter_keeps_old_warning(tmp_path, monkeypatch):
    """An adapter without binary sort support -> the old warning remains."""
    monkeypatch.setattr(SQLiteAdapter, "binary_collation_supported", False)
    run = engine.run(_make_pair(tmp_path))
    t = run.tables[0]
    assert t.warnings and "may differ" in t.warnings[0]
    assert "binary collation applied" not in t.warnings[0]


def test_sqlite_stream_sql_collate_binary():
    """sqlite adapter SQL: COLLATE BINARY is applied to text columns only."""
    ad = SQLiteAdapter(EndpointConfig("sqlite", None, {"path": ":memory:"}))
    try:
        sql, params = ad._stream_sql(
            "t", ["code", "n"], ["code", "n"], order_logicals=["text", "number"])
        assert '"code" COLLATE BINARY' in sql
        assert '"n" COLLATE' not in sql
        assert params == ()
        # without order_logicals - previous behavior, no COLLATE at all
        sql, _ = ad._stream_sql("t", ["code"], ["code"])
        assert "COLLATE" not in sql
    finally:
        ad.close()


def test_sqlite_stream_rows_binary_order(tmp_path):
    """stream_rows with order_logicals really sorts by UTF-8 bytes."""
    cfg = _make_pair(tmp_path)
    ad = SQLiteAdapter(cfg.source)
    try:
        rows = list(ad.stream_rows("t", ["code", "v"], ["code"], batch=2,
                                   order_logicals=["text"]))
        assert [r[0] for r in rows] == _BINARY_ORDER
    finally:
        ad.close()


def test_postgres_query_collate_c():
    """postgres adapter SQL: COLLATE "C" only on text order_by columns."""
    pytest.importorskip("psycopg")
    from dbparity.adapters.postgres_adapter import PostgresAdapter

    q, params = PostgresAdapter._build_stream_query(
        "public", "t", ["code", "n"], ["code", "n"],
        order_logicals=["text", "number"])
    s = q.as_string()
    assert '"code" COLLATE "C"' in s
    assert '"n" COLLATE' not in s          # COLLATE on non-text is a PG error
    assert params == ()
    # pk_range and order_logicals are compatible; no order_logicals - no COLLATE
    q, params = PostgresAdapter._build_stream_query(
        "public", "t", ["code"], ["code"],
        pk_range=("code", "a", "z"), order_logicals=["text"])
    s = q.as_string()
    assert "WHERE" in s and 'COLLATE "C"' in s
    assert params == ("a", "z")
    q, _ = PostgresAdapter._build_stream_query("public", "t", ["code"], ["code"])
    assert "COLLATE" not in q.as_string()
