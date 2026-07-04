"""Benchmark smoke test: small N + --json mode.

Verifies the JSON output contract that the Benchmark CI workflow
(bench.yml) relies on: valid JSON, full key set, correct counters.
Speed is not checked here - the thresholds live in the workflow itself.
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
    # hash mode covered every row; streamed no more than the total
    assert data["hash_total_rows"] == 2 * N
    assert 0 < data["hash_rows_streamed"] <= data["hash_total_rows"]
