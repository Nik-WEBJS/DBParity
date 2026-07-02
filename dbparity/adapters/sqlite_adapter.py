"""SQLite-адаптер: демо, тесты и эмуляция диалектов.

Опция dialect_emulation позволяет прогнать диалект-специфичные правила
нормализации (например, oracle: ''==NULL) на sqlite-данных.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Iterator, List, Sequence

from .base import Adapter, ColumnSchema, TableSchema


def _logical(raw: str) -> str:
    u = (raw or "").upper()
    if "INT" in u:
        return "number"
    if "BOOL" in u:
        return "bool"
    if any(x in u for x in ("CHAR", "TEXT", "CLOB")):
        return "text"
    if "BLOB" in u:
        return "bytes"
    if any(x in u for x in ("REAL", "FLOA", "DOUB")):
        return "float"
    if "DATE" in u or "TIME" in u:
        return "datetime"
    if any(x in u for x in ("NUM", "DEC")):
        return "number"
    return "text"


def _maybe_temporal(v):
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            try:
                return date.fromisoformat(v)
            except ValueError:
                return v
    return v


class SQLiteAdapter(Adapter):
    def __init__(self, endpoint):
        super().__init__(endpoint)
        path = endpoint.options.get("path")
        if not path:
            raise ValueError("sqlite: в конфиге требуется параметр path")
        self.path = path
        self.conn = sqlite3.connect(path)
        self.dialect = endpoint.options.get("dialect_emulation") or "sqlite"

    @property
    def label(self) -> str:
        return self.endpoint.label or f"sqlite:{self.path}"

    @staticmethod
    def _quote(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def list_tables(self) -> List[str]:
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r[0] for r in cur.fetchall()]

    def table_schema(self, table: str) -> TableSchema:
        cur = self.conn.execute(f"PRAGMA table_info({self._quote(table)})")
        cols, pk = [], []
        for _cid, name, ctype, _notnull, _default, pk_pos in cur.fetchall():
            cols.append(ColumnSchema(name=name, logical=_logical(ctype), raw=ctype or ""))
            if pk_pos:
                pk.append((pk_pos, name))
        return TableSchema(name=table, columns=cols,
                           pk=[n for _, n in sorted(pk)])

    def stream_rows(
        self, table: str, columns: Sequence[str],
        order_by: Sequence[str], batch: int,
    ) -> Iterator[tuple]:
        schema = self.table_schema(table)
        logical = {c.name: c.logical for c in schema.columns}
        temporal_idx = [i for i, c in enumerate(columns)
                        if logical.get(c) in ("datetime", "date")]
        cols_sql = ", ".join(self._quote(c) for c in columns)
        order_sql = ", ".join(self._quote(c) for c in order_by)
        cur = self.conn.cursor()
        cur.execute(f"SELECT {cols_sql} FROM {self._quote(table)} ORDER BY {order_sql}")
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            for row in rows:
                if temporal_idx:
                    row = tuple(
                        _maybe_temporal(v) if i in temporal_idx else v
                        for i, v in enumerate(row)
                    )
                yield row

    def close(self) -> None:
        self.conn.close()
