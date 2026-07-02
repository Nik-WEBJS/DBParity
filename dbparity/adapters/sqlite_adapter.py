"""SQLite-адаптер: демо, тесты и эмуляция диалектов.

Опция dialect_emulation позволяет прогнать диалект-специфичные правила
нормализации (например, oracle: ''==NULL) на sqlite-данных.
Digest-функции (md5hex/hex2int) регистрируются как Python-функции.
"""
from __future__ import annotations

import hashlib
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


def _md5hex(s):
    if s is None:
        return None
    return hashlib.md5(str(s).encode("utf-8")).hexdigest()


def _hex2int(s):
    return int(s, 16) if s else 0


class SQLiteAdapter(Adapter):
    supports_digest = True

    def __init__(self, endpoint):
        super().__init__(endpoint)
        path = endpoint.options.get("path")
        if not path:
            raise ValueError("sqlite: в конфиге требуется параметр path")
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.create_function("md5hex", 1, _md5hex, deterministic=True)
        self.conn.create_function("hex2int", 1, _hex2int, deterministic=True)
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
        pk_range=None,
    ) -> Iterator[tuple]:
        schema = self.table_schema(table)
        logical = {c.name: c.logical for c in schema.columns}
        temporal_idx = [i for i, c in enumerate(columns)
                        if logical.get(c) in ("datetime", "date")]
        cols_sql = ", ".join(self._quote(c) for c in columns)
        order_sql = ", ".join(self._quote(c) for c in order_by)
        where, params = "", ()
        if pk_range is not None:
            col, lo, hi = pk_range
            if hi is None:      # открытый диапазон — для resume с watermark
                where = f" WHERE {self._quote(col)} >= ?"
                params = (lo,)
            else:
                where = (f" WHERE {self._quote(col)} >= ? "
                         f"AND {self._quote(col)} <= ?")
                params = (lo, hi)
        cur = self.conn.cursor()
        cur.execute(f"SELECT {cols_sql} FROM {self._quote(table)}{where} "
                    f"ORDER BY {order_sql}", params)
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

    # ---- digest-API ----------------------------------------------------------

    def _canon(self, col: str, logical: str, rtrim: bool) -> str:
        q = self._quote(col)
        if logical == "number":
            # инъективно; целые без '.0', чтобы совпадать с trim_scale (PG)
            # и TM9 (Oracle): 100.0 → '100', 1.5 → '1.5'
            return (f"CASE WHEN {q} IS NULL THEN 'N' "
                    f"WHEN CAST({q} AS INTEGER) = {q} "
                    f"THEN CAST(CAST({q} AS INTEGER) AS TEXT) "
                    f"ELSE CAST({q} AS TEXT) END")
        if logical == "bool":
            return (f"CASE WHEN {q} IS NULL THEN 'N' "
                    f"WHEN {q} THEN '1' ELSE '0' END")
        v = f"RTRIM({q}, ' ')" if rtrim else q
        return f"CASE WHEN {q} IS NULL THEN 'N' ELSE md5hex({v}) END"

    def pk_bounds(self, table: str, pk_col: str):
        q = self._quote(pk_col)
        row = self.conn.execute(
            f"SELECT MIN({q}), MAX({q}) FROM {self._quote(table)} "
            f"WHERE {q} IS NOT NULL").fetchone()
        return (row[0], row[1]) if row else (None, None)

    def null_pk_count(self, table: str, pk_col: str) -> int:
        q = self._quote(pk_col)
        return self.conn.execute(
            f"SELECT COUNT(*) FROM {self._quote(table)} WHERE {q} IS NULL"
        ).fetchone()[0]

    def bucket_digests(self, table: str, columns, logicals, pk_col: str,
                       lo, step: int, hi, rtrim: bool = False) -> dict:
        parts = " || '|' || ".join(
            self._canon(c, lg, rtrim) for c, lg in zip(columns, logicals))
        q = self._quote(pk_col)
        sql = (
            f"SELECT b, COUNT(*), "
            f"COALESCE(SUM(hex2int(substr(h, 1, 8))), 0), "
            f"COALESCE(SUM(hex2int(substr(h, 9, 8))), 0), "
            f"COALESCE(SUM(hex2int(substr(h, 17, 8))), 0) "
            f"FROM (SELECT CAST(({q} - ?) / ? AS INTEGER) AS b, "
            f"md5hex({parts}) AS h FROM {self._quote(table)} "
            f"WHERE {q} >= ? AND {q} <= ?) GROUP BY b"
        )
        out = {}
        for b, c, s1, s2, s3 in self.conn.execute(sql, (lo, step, lo, hi)):
            out[int(b)] = (int(c), int(s1), int(s2), int(s3))
        return out

    def close(self) -> None:
        self.conn.close()
