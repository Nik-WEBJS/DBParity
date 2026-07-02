"""Smoke-тест бенчмарка: маленький N + режим --json.

Проверяет контракт JSON-вывода, на который опирается CI-workflow Benchmark
(bench.yml): валидный JSON, полный набор ключей, корректные счётчики.
Скорость здесь не проверяется — пороги живут в самом workflow.
"""
import json
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent / "bench" / "bench.py"

N = 20_000
EXPECTED_KEYS = {"n", "generic_rows_per_s", "fastpath_rows_per_s",
                 "hash_rows_streamed", "hash_total_rows", "diffs_ok"}


def test_bench_json_smoke(tmp_path):
    out = tmp_path / "bench.json"
    proc = subprocess.run(
        [sys.executable, str(BENCH), str(N), "--json", str(out)],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout

    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == EXPECTED_KEYS
    assert data["n"] == N
    assert data["diffs_ok"] is True
    assert data["generic_rows_per_s"] > 0
    assert data["fastpath_rows_per_s"] > 0
    # hash-режим прошёл по всем строкам, потоково — не больше, чем всего
    assert data["hash_total_rows"] == 2 * N
    assert 0 < data["hash_rows_streamed"] <= data["hash_total_rows"]
