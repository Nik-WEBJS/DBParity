"""Чекпоинты: атомарный JSON-стейт для продолжения сверки после обрыва.

Схема: завершённые таблицы сохраняются целиком; для текущей таблицы
(stream-режим, одноколоночный PK) периодически пишется watermark —
значение PK, ниже которого всё учтено. Возобновление читает счётчики
и продолжает потоки с `WHERE pk >= watermark`.

Файл валиден только для того же конфига (fingerprint) — смена правил,
таблиц или эндпоинтов делает старый стейт бессмысленным.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .models import DiffKind, RowDiff, TableResult

STATE_VERSION = 1

_COUNTER_FIELDS = (
    "src_rows", "dst_rows", "matched", "mismatched", "missing_in_target",
    "extra_in_target", "duplicate_pk", "null_pk", "duration_s",
    "rows_hash_matched", "rows_streamed", "segments_matched",
    "segments_streamed",
)


def config_fingerprint(config) -> str:
    s = config.summary()
    payload = {
        "source": s["source"], "target": s["target"], "rules": s["rules"],
        "strategy": s["strategy"], "tables": config.tables,
        "pk_overrides": config.pk_overrides,
        "exclude_columns": config.exclude_columns,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _wm_encode(v) -> Optional[dict]:
    """Watermark сериализуем только для «безопасных» типов PK."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return {"k": "int", "v": str(v)}
    if isinstance(v, Decimal):
        if v == v.to_integral_value():
            return {"k": "int", "v": str(int(v))}
        return None     # неинтегральный numeric: float-граница рискованна
    if isinstance(v, str):
        return {"k": "str", "v": v}
    return None


def _wm_decode(d: dict):
    return int(d["v"]) if d["k"] == "int" else d["v"]


def table_result_from_dict(d: dict) -> TableResult:
    tr = TableResult(table=d["table"], pk=list(d.get("pk", [])))
    for f in _COUNTER_FIELDS:
        setattr(tr, f, d.get(f, 0))
    tr.mode = d.get("mode", "stream")
    tr.error = d.get("error")
    tr.warnings = list(d.get("warnings", []))
    tr.column_mismatch_counts = dict(d.get("column_mismatch_counts", {}))
    tr.samples = [
        RowDiff(kind=DiffKind(s["kind"]), pk=tuple(s["pk"]),
                columns=({k: tuple(v) for k, v in s["columns"].items()}
                         if s.get("columns") else None))
        for s in d.get("samples", [])
    ]
    return tr


class Checkpointer:
    def __init__(self, path, fingerprint: str):
        self.path = Path(path)
        self.fp = fingerprint
        self._lock = threading.Lock()
        # partial — незавершённые таблицы (слот на таблицу: упавшая таблица
        # не теряет свой watermark из-за соседей, идущих следом)
        self._state = {"version": STATE_VERSION, "fingerprint": fingerprint,
                       "done": {}, "partial": {}}
        self.resumed_tables: set = set()

    @classmethod
    def load_or_create(cls, path, fingerprint: str, resume: bool) -> "Checkpointer":
        ck = cls(path, fingerprint)
        if resume and ck.path.exists():
            try:
                data = json.loads(ck.path.read_text(encoding="utf-8"))
                if (data.get("version") == STATE_VERSION
                        and data.get("fingerprint") == fingerprint):
                    ck._state = data
                    ck.resumed_tables = set(data.get("done", {}))
            except (OSError, json.JSONDecodeError, KeyError):
                pass    # битый/чужой файл — начинаем заново
        return ck

    # ---- чтение -------------------------------------------------------------

    def done_table(self, table: str) -> Optional[TableResult]:
        d = self._state["done"].get(table)
        return table_result_from_dict(d) if d else None

    def current_snapshot(self, table: str):
        """(TableResult, watermark) для прерванной таблицы, либо None."""
        cur = self._state.get("partial", {}).get(table)
        if cur and cur.get("watermark"):
            return (table_result_from_dict(cur["result"]),
                    _wm_decode(cur["watermark"]))
        return None

    # ---- запись -------------------------------------------------------------

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self._state, ensure_ascii=False, default=str),
            encoding="utf-8")
        os.replace(tmp, self.path)      # атомарная подмена

    def snapshot(self, table: str, tr: TableResult, watermark) -> None:
        enc = _wm_encode(watermark)
        if enc is None:
            return
        with self._lock:
            self._state["partial"][table] = {"watermark": enc,
                                             "result": asdict(tr)}
            self._save()

    def table_done(self, tr: TableResult) -> None:
        with self._lock:
            self._state["done"][tr.table] = asdict(tr)
            self._state.get("partial", {}).pop(tr.table, None)
            self._save()

    def finish(self, delete: bool = True) -> None:
        if not delete:
            return
        with self._lock:
            try:
                self.path.unlink()
            except OSError:
                pass
