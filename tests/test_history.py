"""Incremental run history and the drift timeline (`dbparity history`)."""
import json
import sqlite3

from dbparity import cli
from dbparity.config import Config, EndpointConfig
from dbparity.core import engine, incremental
from dbparity.core.incremental import (IncrementalState, default_state_path,
                                       state_fingerprint)
from dbparity.report.render import render_timeline_html


def _entry(ts: str, diffs: int, full: bool = False) -> dict:
    return {"ts": ts, "full": full, "equivalent": diffs == 0,
            "tables": {"t": {"total_diffs": diffs, "mismatched": diffs,
                             "missing_in_target": 0, "extra_in_target": 0,
                             "src_rows": 10}}}


def _build_pair(tmp_path):
    """A pair of sqlite DBs with a numeric watermark column updated_at."""
    for name in ("s.db", "d.db"):
        conn = sqlite3.connect(tmp_path / name)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT,"
                     " updated_at INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?,?,?)",
                         [(i, f"v{i}", 100 + i) for i in range(1, 6)])
        conn.commit()
        conn.close()

    def cfg(**kw):
        return Config(
            source=EndpointConfig("sqlite", "S", {"path": str(tmp_path / "s.db")}),
            target=EndpointConfig("sqlite", "D", {"path": str(tmp_path / "d.db")}),
            incremental={"t": "updated_at"}, **kw)
    return cfg


# ---- state ------------------------------------------------------------------

def test_record_run_roundtrip(tmp_path):
    p = tmp_path / "st.json"
    st = IncrementalState.load_or_create(p, "fp")
    st.record_run(_entry("2026-07-03T10:00:00+00:00", 5))
    st.record_run(_entry("2026-07-03T11:00:00+00:00", 0))
    st2 = IncrementalState.load_or_create(p, "fp")
    assert len(st2.history) == 2
    assert st2.history[-1]["equivalent"] is True


def test_history_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(incremental, "HISTORY_LIMIT", 5)
    st = IncrementalState.load_or_create(tmp_path / "st.json", "fp")
    for i in range(8):
        st.record_run(_entry(f"2026-07-03T0{i}:00:00+00:00", i))
    assert len(st.history) == 5
    assert st.history[0]["tables"]["t"]["total_diffs"] == 3   # oldest trimmed


def test_old_state_without_history(tmp_path):
    """A state file predating history support loads with an empty journal."""
    p = tmp_path / "st.json"
    p.write_text(json.dumps({"version": 1, "fingerprint": "fp",
                             "tables": {"t": {"k": "int", "v": "105"}}}),
                 encoding="utf-8")
    st = IncrementalState.load_or_create(p, "fp")
    assert st.history == []
    assert st.last_watermark("t") == 105


# ---- engine writes history ----------------------------------------------------

def test_engine_appends_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = _build_pair(tmp_path)()
    engine.run(cfg)                          # full first run

    for name in ("s.db", "d.db"):            # synchronized mutation of one row
        conn = sqlite3.connect(tmp_path / name)
        conn.execute("UPDATE t SET v='new', updated_at=200 WHERE id=3")
        conn.commit()
        conn.close()
    engine.run(cfg)                          # incremental second run

    ifp = state_fingerprint(cfg)
    st = IncrementalState.load_or_create(default_state_path(ifp), ifp)
    hist = st.history
    assert len(hist) == 2
    assert hist[0]["tables"]["t"]["src_rows"] == 5
    # the changed row + the boundary row (the wm >= last filter is inclusive -
    # a safeguard against concurrent writes right at the watermark boundary)
    assert hist[1]["tables"]["t"]["src_rows"] == 2
    assert hist[1]["equivalent"] is True


# ---- rendering and CLI --------------------------------------------------------

def test_render_timeline_html():
    hist = [_entry("2026-07-03T10:00:00+00:00", 7),
            _entry("2026-07-03T11:00:00+00:00", 2),
            _entry("2026-07-03T12:00:00+00:00", 0)]
    html = render_timeline_html(hist, "Oracle PROD", "PG NEW")
    assert "ZERO DRIFT" in html
    assert "Oracle PROD" in html and "PG NEW" in html
    assert "tabler" in html and "chart.umd" in html
    assert '"total": [7, 2, 0]' in html.replace("'", '"') or "7, 2, 0" in html

    html_red = render_timeline_html(hist[:2], "S", "D")
    assert "ZERO DRIFT" not in html_red


def test_cli_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_factory = _build_pair(tmp_path)
    cfg = cfg_factory()
    # YAML config for the CLI (same incremental map -> same fingerprint)
    cfg_yaml = tmp_path / "cfg.yaml"
    cfg_yaml.write_text(
        "source: {type: sqlite, label: S, path: %s}\n"
        "target: {type: sqlite, label: D, path: %s}\n"
        "incremental: {t: updated_at}\n"
        % (tmp_path / "s.db", tmp_path / "d.db"),
        encoding="utf-8")

    rc_empty = cli.main(["history", "-c", str(cfg_yaml)])
    assert rc_empty == 2                     # no history yet

    engine.run(cfg)                          # one entry now exists
    out_html = tmp_path / "timeline.html"
    rc = cli.main(["history", "-c", str(cfg_yaml), "--html", str(out_html)])
    assert rc == 0
    assert out_html.exists()
    assert "Drift timeline" in out_html.read_text(encoding="utf-8")
