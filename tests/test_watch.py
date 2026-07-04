"""Watch mode (`dbparity watch`): run until drift is stably zero."""
import sqlite3

from dbparity import cli


def _make_pair(tmp_path):
    for name in ("s.db", "d.db"):
        conn = sqlite3.connect(tmp_path / name)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT,"
                     " updated_at INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?,?,?)",
                         [(i, f"v{i}", 100 + i) for i in range(1, 6)])
        conn.commit()
        conn.close()
    cfg_yaml = tmp_path / "cfg.yaml"
    cfg_yaml.write_text(
        "source: {type: sqlite, label: S, path: %s}\n"
        "target: {type: sqlite, label: D, path: %s}\n"
        "incremental: {t: updated_at}\n"
        % (tmp_path / "s.db", tmp_path / "d.db"),
        encoding="utf-8")
    return cfg_yaml


def test_watch_requires_incremental(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_pair(tmp_path)
    plain = tmp_path / "plain.yaml"
    plain.write_text(
        "source: {type: sqlite, path: %s}\ntarget: {type: sqlite, path: %s}\n"
        % (tmp_path / "s.db", tmp_path / "d.db"), encoding="utf-8")
    rc = cli.main(["watch", "-c", str(plain), "--interval", "0"])
    assert rc == 2


def test_watch_green_when_no_drift(tmp_path, monkeypatch):
    """Identical databases: two zero-drift runs in a row -> exit code 0."""
    monkeypatch.chdir(tmp_path)
    cfg_yaml = _make_pair(tmp_path)
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    rc = cli.main(["watch", "-c", str(cfg_yaml),
                   "--interval", "0", "--stable", "2", "--max-runs", "10"])
    assert rc == 0
    assert len(sleeps) == 1          # exactly one pause between the two runs


def test_watch_red_on_persistent_drift(tmp_path, monkeypatch):
    """Source-only drift never settles -> the run limit hits, exit code 1."""
    monkeypatch.chdir(tmp_path)
    cfg_yaml = _make_pair(tmp_path)

    calls = {"n": 0}

    def sleep_and_drift(_s):
        # after the first run, inject drift: a row updated ONLY in src
        calls["n"] += 1
        if calls["n"] == 1:
            conn = sqlite3.connect(tmp_path / "s.db")
            conn.execute("UPDATE t SET v='drift', updated_at=200 WHERE id=2")
            conn.commit()
            conn.close()

    monkeypatch.setattr("time.sleep", sleep_and_drift)
    rc = cli.main(["watch", "-c", str(cfg_yaml),
                   "--interval", "0", "--stable", "3", "--max-runs", "4"])
    assert rc == 1
    assert calls["n"] == 3           # pauses between the four runs
