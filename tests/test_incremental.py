"""Incremental mode (v0.5): watermark filter, state, drift detection."""
import dataclasses
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import yaml

from dbparity import cli
from dbparity.config import (Config, EndpointConfig, config_from_dict,
                             validate_config_dict)
from dbparity.core import engine
from dbparity.core.incremental import (IncrementalState, default_state_path,
                                       state_fingerprint)

# updated_at is a numeric watermark (more deterministic than timestamps);
# values are distinct so that the >= wm filter captures as few rows as possible
ROWS = [(1, "a", 101), (2, "b", 102), (3, "c", 103), (4, "d", 104), (5, "e", 105)]


def _mkdb(path, rows=ROWS) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, "
                 "v TEXT, updated_at INTEGER)")
    conn.executemany("INSERT INTO items VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _touch(path, row_id: int, v: str, wm: int) -> None:
    """Updates a row: new value + advances the watermark column."""
    conn = sqlite3.connect(path)
    conn.execute("UPDATE items SET v = ?, updated_at = ? WHERE id = ?",
                 (v, wm, row_id))
    conn.commit()
    conn.close()


def _cfg(tmp_path, **overrides) -> Config:
    params = dict(
        source=EndpointConfig("sqlite", None, {"path": str(tmp_path / "s.db")}),
        target=EndpointConfig("sqlite", None, {"path": str(tmp_path / "d.db")}),
        incremental={"items": "updated_at"},
    )
    params.update(overrides)
    return Config(**params)


def _setup(tmp_path, monkeypatch) -> Config:
    """Two identical DBs + chdir: the auto-state .dbparity_incr_* goes to tmp."""
    monkeypatch.chdir(tmp_path)
    _mkdb(tmp_path / "s.db")
    _mkdb(tmp_path / "d.db")
    return _cfg(tmp_path)


def _items(run):
    return {t.table: t for t in run.tables}["items"]


def _saved_wm(cfg):
    """Watermark from the on-disk state file (freshly loaded)."""
    fp = state_fingerprint(cfg)
    return IncrementalState.load_or_create(
        default_state_path(fp), fp).last_watermark("items")


# ---- integration (sqlite) ---------------------------------------------------

def test_first_run_full_and_state_created(tmp_path, monkeypatch):
    """Run 1 (no state yet): everything compared, state created with max(updated_at)."""
    cfg = _setup(tmp_path, monkeypatch)
    tr = _items(engine.run(cfg))
    assert tr.error is None
    assert tr.src_rows == 5 and tr.dst_rows == 5 and tr.matched == 5
    assert Path(default_state_path(state_fingerprint(cfg))).exists()
    assert _saved_wm(cfg) == 105
    assert cfg.summary()["incremental"] == {"items": "updated_at"}


def test_second_run_checks_only_changed(tmp_path, monkeypatch):
    """A synchronized update in both DBs: run 2 compares only that row."""
    cfg = _setup(tmp_path, monkeypatch)
    engine.run(cfg)                                 # full run + state
    for db in ("s.db", "d.db"):
        _touch(tmp_path / db, 5, "e2", 106)
    tr = _items(engine.run(cfg))
    assert tr.error is None
    assert tr.src_rows == 1 and tr.dst_rows == 1    # only the changed row
    assert tr.matched == 1 and tr.total_diffs == 0
    assert any("Incremental run" in w for w in tr.warnings)
    assert _saved_wm(cfg) == 106                    # watermark advanced


def test_drift_detected_as_missing(tmp_path, monkeypatch):
    """A row updated only in the source -> missing_in_target (drift)."""
    cfg = _setup(tmp_path, monkeypatch)
    engine.run(cfg)
    _touch(tmp_path / "s.db", 2, "B!", 110)         # dual-write lost the record
    tr = _items(engine.run(cfg))
    assert tr.error is None
    assert tr.missing_in_target == 1 and tr.total_diffs == 1
    assert any("dual-write drift" in w for w in tr.warnings)
    assert _saved_wm(cfg) == 110


def test_full_flag_rechecks_everything(tmp_path, monkeypatch):
    """full=True: the filter is ignored, everything is compared, state is updated."""
    cfg = _setup(tmp_path, monkeypatch)
    engine.run(cfg)
    for db in ("s.db", "d.db"):
        _touch(tmp_path / db, 1, "a2", 107)
    tr = _items(engine.run(cfg, full=True))
    assert tr.src_rows == 5 and tr.dst_rows == 5    # everything compared
    assert tr.matched == 5 and tr.total_diffs == 0
    assert any("watermark ignored" in w for w in tr.warnings)
    assert _saved_wm(cfg) == 107                    # state updated regardless


def test_fingerprint_change_ignores_state(tmp_path, monkeypatch):
    """A config change alters the fingerprint -> the old state is not used."""
    cfg = _setup(tmp_path, monkeypatch)
    engine.run(cfg)
    assert _saved_wm(cfg) == 105
    cfg2 = dataclasses.replace(cfg, strategy="stream")
    assert state_fingerprint(cfg2) != state_fingerprint(cfg)
    # changing the watermark column invalidates the state as well
    assert state_fingerprint(
        dataclasses.replace(cfg, incremental={"items": "id"})
    ) != state_fingerprint(cfg)
    tr = _items(engine.run(cfg2))
    assert tr.src_rows == 5                         # full comparison from scratch
    # unit: same path, foreign fingerprint -> the watermark is not visible
    st = IncrementalState.load_or_create(
        default_state_path(state_fingerprint(cfg)), "different-fingerprint")
    assert st.last_watermark("items") is None


def test_missing_watermark_column_is_error(tmp_path, monkeypatch):
    """A nonexistent watermark column -> an error result with a clear message."""
    monkeypatch.chdir(tmp_path)
    _mkdb(tmp_path / "s.db")
    _mkdb(tmp_path / "d.db")
    cfg = _cfg(tmp_path, incremental={"items": "no_such_col"})
    tr = _items(engine.run(cfg))
    assert tr.status == "error"
    assert "no_such_col" in tr.error and "watermark" in tr.error.lower()


def test_incremental_beats_hash(tmp_path, monkeypatch):
    """A digest-eligible table that is in incremental -> stream path + a note."""
    cfg = dataclasses.replace(_setup(tmp_path, monkeypatch), strategy="hash")
    # control: without incremental this table does take the hash path
    assert _items(engine.run(
        dataclasses.replace(cfg, incremental={}))).mode == "hash"
    tr = _items(engine.run(cfg))
    assert tr.error is None and tr.mode == "stream"
    assert any("hash comparison disabled" in w for w in tr.warnings)
    assert _saved_wm(cfg) == 105                    # tracking still worked


# ---- state unit tests ---------------------------------------------------------

def test_state_unit_roundtrip_and_atomicity(tmp_path):
    """State file: write/reload, atomicity, corrupted data."""
    p = tmp_path / "incr.json"
    st = IncrementalState.load_or_create(p, "fp")
    assert st.last_watermark("t") is None           # empty state
    st.update("t", Decimal(42))                     # integral Decimal is fine
    assert st.last_watermark("t") == 42
    assert not p.with_suffix(".json.tmp").exists()  # tmp file swapped into place
    assert json.loads(p.read_text(encoding="utf-8"))["fingerprint"] == "fp"

    st2 = IncrementalState.load_or_create(p, "fp")  # reload
    assert st2.last_watermark("t") == 42
    st2.update("t", 5.5)                            # float is not encodable...
    assert st2.last_watermark("t") == 42            # ...the old wm is kept

    p.write_text("{truncated", encoding="utf-8")    # corrupted file
    st3 = IncrementalState.load_or_create(p, "fp")
    assert st3.last_watermark("t") is None          # clean state, no exceptions


# ---- config and CLI -----------------------------------------------------------

def test_config_parsing_validation_and_cli_full(tmp_path, monkeypatch):
    """Parsing/validating the incremental map and passing --full through the CLI."""
    monkeypatch.chdir(tmp_path)
    _mkdb(tmp_path / "s.db")
    _mkdb(tmp_path / "d.db")
    conf = {
        "source": {"type": "sqlite", "path": str(tmp_path / "s.db")},
        "target": {"type": "sqlite", "path": str(tmp_path / "d.db")},
        "incremental": {"Items": "Updated_At"},     # case normalization
    }
    assert validate_config_dict(conf) == []
    assert config_from_dict(conf).incremental == {"items": "updated_at"}
    # invalid variants are caught with the field path
    assert any(p.startswith("incremental.items")
               for p in validate_config_dict(dict(conf,
                                                  incremental={"items": 5})))
    assert any(p.startswith("incremental:")
               for p in validate_config_dict(dict(conf,
                                                  incremental=["items"])))

    (tmp_path / "c.yaml").write_text(yaml.safe_dump(conf), encoding="utf-8")
    assert cli.main(["compare", "-c", str(tmp_path / "c.yaml")]) == 0
    # drift only in the source: --full compares everything and finds it
    _touch(tmp_path / "s.db", 2, "B!", 110)
    assert cli.main(["compare", "-c", str(tmp_path / "c.yaml"), "--full"]) == 1
