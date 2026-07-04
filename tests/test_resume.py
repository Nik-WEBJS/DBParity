"""Resilience: checkpoints, resume after an interruption, retries."""
import dataclasses
from decimal import Decimal

from dbparity.core import engine
from dbparity.core.checkpoint import (Checkpointer, _wm_encode,
                                      config_fingerprint)
from dbparity.core.models import DiffKind, RowDiff, TableResult
from dbparity.demo.seed import build_demo


def _by_table(run):
    return {t.table: (t.total_diffs, t.matched, t.src_rows, t.dst_rows,
                      tuple(sorted(t.column_mismatch_counts.items())))
            for t in run.tables}


def _make_flaky(real_build, state):
    """Adapter wrapper: the orders stream breaks after fail_after rows (once)."""
    def patched(ep):
        ad = real_build(ep)
        orig_stream = ad.stream_rows

        def stream(table, columns, order_by, batch, pk_range=None):
            it = orig_stream(table, columns, order_by, batch, pk_range=pk_range)
            if table != "orders" or not state["armed"]:
                return it

            def gen():
                for i, row in enumerate(it):
                    if i >= state["fail_after"]:
                        state["armed"] = False
                        raise RuntimeError("simulated network drop")
                    yield row
            return gen()

        ad.stream_rows = stream
        return ad
    return patched


# ---- unit tests ------------------------------------------------------------

def test_fingerprint_stable_and_sensitive(tmp_path):
    cfg = build_demo(tmp_path)
    fp = config_fingerprint(cfg)
    assert fp == config_fingerprint(cfg)
    assert config_fingerprint(dataclasses.replace(cfg, strategy="hash")) != fp


def test_wm_encode_safety():
    assert _wm_encode(Decimal("42")) == {"k": "int", "v": "42"}
    assert _wm_encode(7)["v"] == "7"
    assert _wm_encode("abc") == {"k": "str", "v": "abc"}
    assert _wm_encode(Decimal("5.5")) is None    # non-integral - too risky
    assert _wm_encode(True) is None


def test_checkpointer_roundtrip(tmp_path):
    p = tmp_path / "ck.json"
    ck = Checkpointer.load_or_create(p, "fp", resume=False)
    tr = TableResult(table="t", pk=["id"], matched=10,
                     samples=[RowDiff(kind=DiffKind.MISMATCH, pk=("1",),
                                      columns={"v": ("a", "b")})])
    ck.snapshot("t", tr, Decimal(42))

    ck2 = Checkpointer.load_or_create(p, "fp", resume=True)
    got, wm = ck2.current_snapshot("t")
    assert wm == 42 and got.matched == 10
    assert got.samples[0].kind == DiffKind.MISMATCH
    assert got.samples[0].columns == {"v": ("a", "b")}

    ck2.table_done(got)
    assert ck2.done_table("t").matched == 10
    assert ck2.current_snapshot("t") is None

    # a foreign fingerprint is ignored
    ck3 = Checkpointer.load_or_create(p, "other-fp", resume=True)
    assert ck3.done_table("t") is None

    ck2.finish()
    assert not p.exists()


# ---- integration ------------------------------------------------------------

def test_retry_resumes_within_run(tmp_path, monkeypatch):
    """A break at orders row 1500 -> the retry continues from the watermark."""
    ref = engine.run(build_demo(tmp_path / "ref"))

    cfg = dataclasses.replace(
        build_demo(tmp_path / "work"),
        checkpoint=str(tmp_path / "ck.json"),
        checkpoint_every_rows=400,
        retry_attempts=3, retry_backoff_s=0.0)
    state = {"armed": True, "fail_after": 1500}
    monkeypatch.setattr(engine, "build_adapter",
                        _make_flaky(engine.build_adapter, state))

    run = engine.run(cfg)
    assert not state["armed"]                      # the break really happened
    orders = {t.table: t for t in run.tables}["orders"]
    assert orders.error is None
    assert any("Resumed from checkpoint" in w for w in orders.warnings)
    assert _by_table(run) == _by_table(ref)
    assert not (tmp_path / "ck.json").exists()     # success -> file removed


def test_resume_across_runs(tmp_path, monkeypatch):
    """A crash with no retries -> a second run with --resume finishes the job."""
    ref = engine.run(build_demo(tmp_path / "ref"))

    ck_path = tmp_path / "ck.json"
    cfg = dataclasses.replace(
        build_demo(tmp_path / "work"),
        checkpoint=str(ck_path), checkpoint_every_rows=400)
    state = {"armed": True, "fail_after": 1200}
    monkeypatch.setattr(engine, "build_adapter",
                        _make_flaky(engine.build_adapter, state))

    run1 = engine.run(cfg)
    by1 = {t.table: t for t in run1.tables}
    assert by1["orders"].error is not None         # the table failed
    assert by1["customers"].error is None          # its neighbors made it
    assert ck_path.exists()                        # state saved

    run2 = engine.run(cfg, resume=True)            # flaky is disarmed (armed=False)
    by2 = {t.table: t for t in run2.tables}
    assert any("Restored from checkpoint" in w
               for w in by2["customers"].warnings)
    assert any("Resumed from checkpoint" in w for w in by2["orders"].warnings)
    assert _by_table(run2) == _by_table(ref)
    assert run2.totals == ref.totals
    assert not ck_path.exists()                    # success -> file removed
