"""Core comparison benchmark: N rows x 2 sides, generic vs fast-path.

Usage: python3 bench/bench.py [N] [--rebuild] [--hash] [--json PATH]
Databases are cached in /tmp/dbparity_bench (the name depends on N).

--json PATH - after the run, write the metrics machine-readably (for CI):
{"n", "generic_rows_per_s", "fastpath_rows_per_s",
 "hash_rows_streamed", "hash_total_rows", "diffs_ok"}
"""
from __future__ import annotations

import json
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
        bal = round(bal + 0.01, 2)          # N/3000 controlled diffs
    return (i, f"Client {i}", f"user{i}@example.com", bal, i % 2,
            f"2025-01-{1 + i % 28:02d}T12:00:00+00:00",
            "" if i % 7 == 0 else f"note {i}")


def build(path: Path, mutate: bool) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    # NUMERIC (not REAL): the column type-qualifies for hash mode
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


def _json_arg() -> Path | None:
    """Path from the `--json PATH` argument, or None when the flag is absent."""
    if "--json" not in sys.argv:
        return None
    i = sys.argv.index("--json")
    if i + 1 >= len(sys.argv):
        sys.exit("bench: --json requires a file path")
    return Path(sys.argv[i + 1])


def bench(label: str, src: Path, dst: Path, **kw) -> tuple[float, int]:
    """One streaming measurement; returns (duration, diff count)."""
    norm = Normalizer(NormalizeRules(), dialect="oracle")
    t0 = time.perf_counter()
    r = compare_table("t", COLS, ["id"], stream(src), stream(dst),
                      norm, norm, **kw)
    dt = time.perf_counter() - t0
    rate = f"{int(2 * N / dt):,}".replace(",", " ")
    print(f"{label:24s} {dt:7.2f} s   {rate:>11} rows/s   "
          f"diffs={r.total_diffs} (expected {N // 3000})")
    return dt, r.total_diffs


def bench_hash(src: Path) -> tuple:
    """The target hash-mode scenario: nearly identical databases (3 diffs).

    Returns (duration, TableResult) - the metrics are needed for --json.

    Note: in sqlite md5 is a Python function (expensive); on PG/Oracle the
    hashes are computed natively, so the win there is even larger.
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
    print(f"{'hash (3 diffs, DB-side)':24s} {dt:7.2f} s   {rate:>11} rows/s   "
          f"diffs={tr.total_diffs} (only {streamed} rows streamed)")
    return dt, tr


def main() -> None:
    json_path = _json_arg()
    DB_DIR.mkdir(exist_ok=True)
    src, dst = DB_DIR / f"src_v2_{N}.db", DB_DIR / f"dst_v2_{N}.db"
    if "--rebuild" in sys.argv or not (src.exists() and dst.exists()):
        t0 = time.perf_counter()
        build(src, mutate=False)
        build(dst, mutate=True)
        print(f"Seeded {2 * N:,} rows: {time.perf_counter() - t0:.1f} s".replace(",", " "))
    print(f"Rows per side: {N:,}".replace(",", " "))
    expected = N // 3000                    # controlled diffs from gen()
    t_gen, d_gen = bench("generic (isinstance)", src, dst)
    t_fast, d_fast = bench("fast-path (typed)", src, dst,
                           src_logicals=LOGICALS, dst_logicals=LOGICALS)
    # --json needs the hash metrics, so it forces the hash run on large N too
    if N <= 400_000 or "--hash" in sys.argv or json_path is not None:
        t_hash, tr_hash = bench_hash(src)
        print(f"fast-path speedup: x{t_gen / t_fast:.2f}; "
              f"hash mode (3 diffs): x{t_gen / t_hash:.2f}")
        if json_path is not None:
            payload = {
                "n": N,
                "generic_rows_per_s": int(2 * N / t_gen),
                "fastpath_rows_per_s": int(2 * N / t_fast),
                "hash_rows_streamed": tr_hash.rows_streamed,
                "hash_total_rows": tr_hash.src_rows + tr_hash.dst_rows,
                # True when every diff counter matched the expected values
                "diffs_ok": (d_gen == expected and d_fast == expected
                             and tr_hash.total_diffs == 3),
            }
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            print(f"JSON metrics: {json_path}")
    else:
        print(f"fast-path speedup: x{t_gen / t_fast:.2f} "
              f"(hash run for N>400K: add --hash)")


if __name__ == "__main__":
    main()
