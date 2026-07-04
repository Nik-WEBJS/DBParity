"""Incremental mode (v0.5): watermark state between runs.

The dual-write cutover scenario: after a full comparison there is no point
re-scanning millions of rows — it is enough to re-check the rows that have
changed since the previous run. For each table the config defines a
watermark column (`incremental: {orders: updated_at}`) — it exists in
BOTH databases and grows monotonically when a row changes (a timestamp or
a numeric version). After a table compares successfully, the engine records
the maximum of that column; the next run filters both sides with
`wm_col >= watermark` (inclusive boundary: rows sharing the maximum are
re-checked — a deliberate safeguard against "same second" writes).

The state is a JSON file next to the working directory (auto-name
`.dbparity_incr_<fp12>.json`), valid only for the same config: the
fingerprint is built from core.checkpoint.config_fingerprint plus the
incremental map itself (changing the watermark column invalidates the old
values). Writes are atomic (tmp + os.replace) and thread-safe (lock) —
the engine updates the state from worker threads when workers>1.

Watermark serialization matches checkpoints (checkpoint._wm_encode/_wm_decode):
int, str, and integral Decimal are supported; other types (float,
datetime objects) are not saved — the update is silently skipped and the
old watermark stays in force (the safe direction: we re-check more).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from .checkpoint import _wm_decode, _wm_encode, config_fingerprint

STATE_VERSION = 1       # format version of the INCREMENTAL state (not the checkpoint)
HISTORY_LIMIT = 500     # run-history entries kept (older ones are evicted)


def state_fingerprint(config) -> str:
    """Config fingerprint for the incremental state.

    The base is config_fingerprint (endpoints, rules, strategy, tables…),
    plus the config.incremental map: an old watermark for a different column
    is not applicable, so changing it also invalidates the state.
    """
    base = config_fingerprint(config)
    extra = json.dumps(getattr(config, "incremental", {}) or {},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{base}|{extra}".encode("utf-8")).hexdigest()


def default_state_path(fingerprint: str) -> str:
    """Auto-name of the state file (analogous to the checkpoint auto-name)."""
    return f".dbparity_incr_{fingerprint[:12]}.json"


class IncrementalState:
    """Per-table watermarks of the last successful comparison (a JSON file).

    The constructor creates an empty state; reading from disk goes through
    the classmethod load_or_create (a corrupted file or one with a foreign
    fingerprint is silently ignored — we start from a clean state).

    Besides the watermarks the file stores "history" — a chronological log
    of run outcomes (record_run): the drift timeline report is built from
    it (`dbparity history`). The key appeared after format v1 and is
    optional: old files without it load as a state with an empty history.
    """

    def __init__(self, path, fingerprint: str):
        self.path = Path(path)
        self.fp = fingerprint
        self._lock = threading.Lock()
        self._tables: dict = {}     # table → encoded watermark
        self._history: list = []    # run log (record_run entries)

    @classmethod
    def load_or_create(cls, path, fingerprint: str) -> "IncrementalState":
        """Loads the state from disk if the file exists and the fingerprint matches."""
        st = cls(path, fingerprint)
        if st.path.exists():
            try:
                data = json.loads(st.path.read_text(encoding="utf-8"))
                if (data.get("version") == STATE_VERSION
                        and data.get("fingerprint") == fingerprint
                        and isinstance(data.get("tables"), dict)):
                    st._tables = dict(data["tables"])
                    # old files (predating history) — no "history" key;
                    # that is not a format error, just an empty log
                    hist = data.get("history")
                    if isinstance(hist, list):
                        st._history = list(hist)
            except (OSError, json.JSONDecodeError):
                pass    # corrupted/unreadable file — start over
        return st

    # ---- reading ----------------------------------------------------------

    def last_watermark(self, table: str):
        """The table's watermark from the previous run, or None (full comparison)."""
        enc = self._tables.get(table)
        if not isinstance(enc, dict):
            return None
        try:
            return _wm_decode(enc)
        except (KeyError, TypeError, ValueError):
            return None     # hand-edited/corrupted entry — treat as absent

    @property
    def history(self) -> list:
        """Run history (a copy; chronological order, most recent last).

        An element is the summary from record_run: {"ts", "full",
        "equivalent", "tables": {table: drift counters}}.
        """
        return list(self._history)

    # ---- writing ----------------------------------------------------------

    def update(self, table: str, wm) -> None:
        """Records the table's new watermark and saves the file immediately.

        A non-encodable watermark (float, datetime, etc.) is skipped —
        the old value stays, and the next run re-checks more rows.
        """
        enc = _wm_encode(wm)
        if enc is None:
            return
        with self._lock:
            self._tables[table] = enc
            self._save_locked()

    def record_run(self, summary: dict) -> None:
        """Appends the run outcome to the history and saves the file immediately.

        The engine builds the summary at the end of run():
        {"ts": iso-UTC, "full": bool, "equivalent": bool,
         "tables": {table: {"total_diffs", "mismatched",
                            "missing_in_target", "extra_in_target",
                            "src_rows"}}}.
        The log is capped at the last HISTORY_LIMIT entries — the state file
        does not balloon under scheduled comparison (cron during dual-write).
        """
        with self._lock:
            self._history.append(summary)
            if len(self._history) > HISTORY_LIMIT:
                self._history = self._history[-HISTORY_LIMIT:]
            self._save_locked()

    def save(self) -> None:
        """Forces a state write (thread-safe)."""
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        """Atomic write: tmp file + os.replace. Call under the lock."""
        payload = {"version": STATE_VERSION, "fingerprint": self.fp,
                   "tables": self._tables, "history": self._history}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.path)      # atomic swap
