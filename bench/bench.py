"""Бенчмарк ядра сравнения: N строк × 2 стороны, generic vs fast-path.

Запуск: python3 bench/bench.py [N] [--rebuild]
БД кэшируются в /tmp/dbparity_bench (имя зависит от N).
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dbparity.core.compare import compare_table            # noqa: E402
from dbparity.core.normalize import Normalizer, NormalizeRules  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 300_000
DB_DIR = Path("/tmp/dbparity_bench")

COLS = ["id", "name", "email", "balance", "is_active", "created_at", "notes"]
LOGICALS = ["number", "text", "text", "float", "number", "text", "text"]


def gen(i: int, mutate: bool) -> tuple:
    bal = round((i * 7.13) % 9990, 2)
    if mutate and i % 3000 == 0:
        bal = round(bal + 0.01, 2)          # N/3000 контролируемых расхождений
    return (i, f"Клиент {i}", f"user{i}@example.com", bal, i % 2,
            f"2025-01-{1 + i % 28:02d}T12:00:00+00:00",
            "" if i % 7 == 0 else f"note {i}")


def build(path: Path, mutate: bool) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    # NUMERIC (не REAL): колонка проходит по типам в hash-режим
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, email TEXT,"
                 " balance NUMERIC, is_active INTEGER, created_at TEXT, notes TEXT)")
    batch = []
    for i in range(1, N + 1):
        batch.append(gen(i, mutate))
        if len(batch) >= 50_000:
            conn.executemany("INSERT INTO t VALUES (?,?,?,?,?,?,?)", batch)
            batch = []
    if batch:
        conn.executemany("INSERT INTO t VALUES (?,?,?,?,?,?,?)", batch)
    conn.commit()
    conn.close()


def stream(path: Path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("SELECT id,name,email,balance,is_active,created_at,notes "
                "FROM t ORDER BY id")
    while True:
        rows = cur.fetchmany(10_000)
        if not rows:
            break
        yield from rows
    conn.close()


def bench(label: str, src: Path, dst: Path, **kw) -> float:
    norm = Normalizer(NormalizeRules(), dialect="oracle")
    t0 = time.perf_counter()
    r = compare_table("t", COLS, ["id"], stream(src), stream(dst),
                      norm, norm, **kw)
    dt = time.perf_counter() - t0
    rate = f"{int(2 * N / dt):,}".replace(",", " ")
    print(f"{label:24s} {dt:7.2f} c   {rate:>11} строк/с   "
          f"diffs={r.total_diffs} (ожидалось {N // 3000})")
    return dt


def bench_hash(src: Path) -> float:
    """Целевой сценарий hash-режима: почти идентичные БД (3 расхождения).

    Замечание: в sqlite md5 — это Python-функция (дорого); на PG/Oracle
    хэши считаются нативно, там выигрыш ещё больше.
    """
    import shutil

    from dbparity.config import Config, EndpointConfig
    from dbparity.core import engine as _engine

    few = DB_DIR / f"few_v2_{N}.db"
    shutil.copyfile(src, few)
    conn = sqlite3.connect(few)
    conn.execute("UPDATE t SET balance = balance + 0.01 WHERE id IN (?,?,?)",
                 (5, N // 2, N - 5))
    conn.commit()
    conn.close()

    cfg = Config(
        source=EndpointConfig("sqlite", None, {"path": str(src)}),
        target=EndpointConfig("sqlite", None, {"path": str(few)}),
        strategy="hash", hash_leaf_rows=10_000)
    t0 = time.perf_counter()
    run = _engine.run(cfg)
    dt = time.perf_counter() - t0
    tr = run.tables[0]
    rate = f"{int(2 * N / dt):,}".replace(",", " ")
    streamed = f"{tr.rows_streamed:,}".replace(",", " ")
    print(f"{'hash (3 диффа, DB-side)':24s} {dt:7.2f} c   {rate:>11} строк/с   "
          f"diffs={tr.total_diffs} (потоково лишь {streamed} строк)")
    return dt


def main() -> None:
    DB_DIR.mkdir(exist_ok=True)
    src, dst = DB_DIR / f"src_v2_{N}.db", DB_DIR / f"dst_v2_{N}.db"
    if "--rebuild" in sys.argv or not (src.exists() and dst.exists()):
        t0 = time.perf_counter()
        build(src, mutate=False)
        build(dst, mutate=True)
        print(f"Сид {2 * N:,} строк: {time.perf_counter() - t0:.1f} c".replace(",", " "))
    print(f"Строк на сторону: {N:,}".replace(",", " "))
    t_gen = bench("generic (isinstance)", src, dst)
    t_fast = bench("fast-path (по типам)", src, dst,
                   src_logicals=LOGICALS, dst_logicals=LOGICALS)
    if N <= 400_000 or "--hash" in sys.argv:
        t_hash = bench_hash(src)
        print(f"Ускорение fast-path: ×{t_gen / t_fast:.2f}; "
              f"hash-режим (3 диффа): ×{t_gen / t_hash:.2f}")
    else:
        print(f"Ускорение fast-path: ×{t_gen / t_fast:.2f} "
              f"(hash-замер на N>400K: добавьте --hash)")


if __name__ == "__main__":
    main()
