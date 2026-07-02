"""Бинарная коллация для текстовых PK: согласованный порядок merge-сравнения."""
import sqlite3

import pytest

from dbparity.adapters.sqlite_adapter import SQLiteAdapter
from dbparity.config import Config, EndpointConfig
from dbparity.core import engine

# PK со смешанным регистром и не-ASCII: локале-зависимые коллации сортируют
# их по-разному, бинарная (по байтам UTF-8) — всегда одинаково
_CODES = ["a", "B", "Ё", "z", "А"]
_BINARY_ORDER = ["B", "a", "z", "Ё", "А"]   # 0x42 < 0x61 < 0x7A < D081 < D090


def _make_pair(tmp_path):
    """Две одинаковые sqlite-БД с ТЕКСТОВЫМ PK → Config для engine.run."""
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
    """Текстовый PK: бинарная сортировка согласована → ноль расхождений."""
    run = engine.run(_make_pair(tmp_path))
    t = run.tables[0]
    assert t.error is None
    assert t.matched == len(_CODES)
    assert t.total_diffs == 0
    assert run.equivalent
    assert t.warnings and "бинарная сортировка" in t.warnings[0]


def test_unsupported_adapter_keeps_old_warning(tmp_path, monkeypatch):
    """Адаптер без бинарной сортировки → остаётся старое предупреждение."""
    monkeypatch.setattr(SQLiteAdapter, "binary_collation_supported", False)
    run = engine.run(_make_pair(tmp_path))
    t = run.tables[0]
    assert t.warnings and "может различаться" in t.warnings[0]
    assert "применена бинарная" not in t.warnings[0]


def test_sqlite_stream_sql_collate_binary():
    """SQL sqlite-адаптера: COLLATE BINARY — только для текстовых колонок."""
    ad = SQLiteAdapter(EndpointConfig("sqlite", None, {"path": ":memory:"}))
    try:
        sql, params = ad._stream_sql(
            "t", ["code", "n"], ["code", "n"], order_logicals=["text", "number"])
        assert '"code" COLLATE BINARY' in sql
        assert '"n" COLLATE' not in sql
        assert params == ()
        # без order_logicals — прежнее поведение, никакого COLLATE
        sql, _ = ad._stream_sql("t", ["code"], ["code"])
        assert "COLLATE" not in sql
    finally:
        ad.close()


def test_sqlite_stream_rows_binary_order(tmp_path):
    """stream_rows с order_logicals реально сортирует по байтам UTF-8."""
    cfg = _make_pair(tmp_path)
    ad = SQLiteAdapter(cfg.source)
    try:
        rows = list(ad.stream_rows("t", ["code", "v"], ["code"], batch=2,
                                   order_logicals=["text"]))
        assert [r[0] for r in rows] == _BINARY_ORDER
    finally:
        ad.close()


def test_postgres_query_collate_c():
    """SQL postgres-адаптера: COLLATE "C" только у текстовых order_by-колонок."""
    pytest.importorskip("psycopg")
    from dbparity.adapters.postgres_adapter import PostgresAdapter

    q, params = PostgresAdapter._build_stream_query(
        "public", "t", ["code", "n"], ["code", "n"],
        order_logicals=["text", "number"])
    s = q.as_string()
    assert '"code" COLLATE "C"' in s
    assert '"n" COLLATE' not in s          # для нетекстовых COLLATE — ошибка PG
    assert params == ()
    # pk_range и order_logicals совместимы; без order_logicals COLLATE нет
    q, params = PostgresAdapter._build_stream_query(
        "public", "t", ["code"], ["code"],
        pk_range=("code", "a", "z"), order_logicals=["text"])
    s = q.as_string()
    assert "WHERE" in s and 'COLLATE "C"' in s
    assert params == ("a", "z")
    q, _ = PostgresAdapter._build_stream_query("public", "t", ["code"], ["code"])
    assert "COLLATE" not in q.as_string()
