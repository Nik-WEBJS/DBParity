"""Hash-режим: паритет со stream, учёт сегментов, fallback, NULL-PK."""
import dataclasses
import sqlite3

from dbparity.config import Config, EndpointConfig
from dbparity.core import engine
from dbparity.demo.seed import EXPECTED, build_demo

N = 20000


def _build_pair(tmp_path):
    """Только числа и текст (hash-eligible), детерминированные расхождения."""
    src_p, dst_p = tmp_path / "hs.db", tmp_path / "hd.db"
    for path, mutate in ((src_p, False), (dst_p, True)):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE acc (id INTEGER PRIMARY KEY, name TEXT,"
                     " qty INTEGER, price NUMERIC)")
        rows = []
        for i in range(1, N + 1):
            if mutate and i in (15000, 15001, 15002):
                continue                                # missing_in_target: 3
            name = "changed" if (mutate and i == 500) else f"acc {i}"
            qty = (i % 97) + (1 if (mutate and i == 10500) else 0)
            price = round((i * 3.3) % 1000, 2)
            rows.append((i, name, qty, price))
        if mutate:
            rows.append((N + 5, "extra", 1, 1.5))       # extra_in_target: 1
        conn.executemany("INSERT INTO acc VALUES (?,?,?,?)", rows)
        conn.commit()
        conn.close()

    def make_cfg(**kw):
        return Config(
            source=EndpointConfig("sqlite", None, {"path": str(src_p)}),
            target=EndpointConfig("sqlite", None, {"path": str(dst_p)}),
            **kw)
    return make_cfg


def test_hash_parity_with_stream(tmp_path):
    make_cfg = _build_pair(tmp_path)
    stream = engine.run(make_cfg(strategy="stream"))
    hashed = engine.run(make_cfg(strategy="hash", hash_leaf_rows=512))
    s, h = stream.tables[0], hashed.tables[0]
    assert s.mode == "stream" and h.mode == "hash"
    for attr in ("src_rows", "dst_rows", "matched", "mismatched",
                 "missing_in_target", "extra_in_target", "null_pk"):
        assert getattr(h, attr) == getattr(s, attr), attr
    assert h.column_mismatch_counts == s.column_mismatch_counts
    assert h.mismatched == 2 and h.missing_in_target == 3 \
        and h.extra_in_target == 1
    # подавляющая часть строк зачтена по хэшу без передачи
    assert h.rows_hash_matched > 12000
    assert h.rows_streamed < 10000
    assert h.segments_matched > 0 and h.segments_streamed > 0


def test_auto_selects_hash_when_eligible(tmp_path):
    make_cfg = _build_pair(tmp_path)
    run = engine.run(make_cfg())            # strategy=auto по умолчанию
    assert run.tables[0].mode == "hash"


def test_hash_fallback_on_unsupported_types(tmp_path):
    """demo-таблицы содержат float/datetime → авто-fallback в stream."""
    cfg = dataclasses.replace(build_demo(tmp_path), strategy="hash")
    run = engine.run(cfg)
    assert all(t.mode == "stream" for t in run.tables)
    warn = " ".join(w for t in run.tables for w in t.warnings)
    assert "hash-режим недоступен" in warn
    by = {t.table: t for t in run.tables}   # счётчики не пострадали
    for key, exp in EXPECTED["customers"].items():
        assert getattr(by["customers"], key) == exp, key


def test_hash_null_pk(tmp_path):
    # 'INT PRIMARY KEY' (не INTEGER) в sqlite допускает NULL в PK
    src_p, dst_p = tmp_path / "ns.db", tmp_path / "nd.db"
    for path, rows in ((src_p, [(None, "x"), (1, "a"), (2, "b")]),
                       (dst_p, [(1, "a"), (2, "b")])):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO t VALUES (?,?)", rows)
        conn.commit()
        conn.close()
    cfg = Config(
        source=EndpointConfig("sqlite", None, {"path": str(src_p)}),
        target=EndpointConfig("sqlite", None, {"path": str(dst_p)}),
        strategy="hash")
    run = engine.run(cfg)
    t = run.tables[0]
    assert t.mode == "hash"
    assert t.null_pk == 1
    assert t.matched == 2
    assert not run.equivalent


def test_hash_empty_tables(tmp_path):
    for name in ("es.db", "ed.db"):
        conn = sqlite3.connect(tmp_path / name)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.commit()
        conn.close()
    cfg = Config(
        source=EndpointConfig("sqlite", None, {"path": str(tmp_path / "es.db")}),
        target=EndpointConfig("sqlite", None, {"path": str(tmp_path / "ed.db")}),
        strategy="hash")
    run = engine.run(cfg)
    t = run.tables[0]
    assert t.mode == "hash" and t.ok and t.matched == 0
