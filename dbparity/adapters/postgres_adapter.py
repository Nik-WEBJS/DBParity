"""PostgreSQL-адаптер (psycopg3, server-side cursor)."""
from __future__ import annotations

from typing import Iterator, List, Sequence

from .base import Adapter, ColumnSchema, TableSchema

try:
    import psycopg
    from psycopg import sql as _sql
except ImportError:  # pragma: no cover
    psycopg = None
    _sql = None


def _logical(data_type: str) -> str:
    u = (data_type or "").lower()
    if u in ("boolean",):
        return "bool"
    if any(x in u for x in ("smallint", "integer", "bigint", "numeric", "decimal", "money")):
        return "number"
    if any(x in u for x in ("real", "double")):
        return "float"
    if "timestamp" in u or "time" in u:
        return "datetime"
    if u == "date":
        return "date"
    if u == "bytea":
        return "bytes"
    return "text"


class PostgresAdapter(Adapter):
    dialect = "postgres"

    def __init__(self, endpoint):
        if psycopg is None:  # pragma: no cover
            raise RuntimeError("Для postgres установите зависимость: pip install 'psycopg[binary]'")
        super().__init__(endpoint)
        o = endpoint.options
        dsn = o.get("dsn")
        if not dsn:
            parts = {
                "host": o.get("host", "localhost"),
                "port": o.get("port", 5432),
                "dbname": o.get("dbname") or o.get("database"),
                "user": o.get("user"),
                "password": o.get("password"),
            }
            dsn = " ".join(f"{k}={v}" for k, v in parts.items() if v is not None)
        self.schema = o.get("schema", "public")
        # server_side=False — обычный курсор (для окружений без DECLARE CURSOR)
        self.server_side = bool(o.get("server_side", True))
        self.conn = psycopg.connect(dsn)
        # prepare_threshold: null в YAML отключает авто-prepare (нужно для
        # окружений с общей сессией вроде PGlite/пулеров в transaction-режиме)
        if "prepare_threshold" in o:
            self.conn.prepare_threshold = o["prepare_threshold"]

    def list_tables(self) -> List[str]:  # pragma: no cover — нет сервера в CI
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
                (self.schema,),
            )
            return [r[0] for r in cur.fetchall()]

    def table_schema(self, table: str) -> TableSchema:  # pragma: no cover
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (self.schema, table),
            )
            cols = [ColumnSchema(name=n, logical=_logical(t), raw=t)
                    for n, t in cur.fetchall()]
            cur.execute(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = %s::regclass AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
                """,
                (f'"{self.schema}"."{table}"',),
            )
            pk = [r[0] for r in cur.fetchall()]
        return TableSchema(name=table, columns=cols, pk=pk)

    def stream_rows(
        self, table: str, columns: Sequence[str],
        order_by: Sequence[str], batch: int,
        pk_range=None,
    ) -> Iterator[tuple]:  # pragma: no cover
        where = _sql.SQL("")
        params = ()
        if pk_range is not None:
            col, lo, hi = pk_range
            if hi is None:      # открытый диапазон — для resume с watermark
                where = _sql.SQL(" WHERE {c} >= %s").format(c=_sql.Identifier(col))
                params = (lo,)
            else:
                where = _sql.SQL(" WHERE {c} >= %s AND {c} <= %s").format(
                    c=_sql.Identifier(col))
                params = (lo, hi)
        q = _sql.SQL("SELECT {cols} FROM {tbl}{where} ORDER BY {order}").format(
            cols=_sql.SQL(", ").join(_sql.Identifier(c) for c in columns),
            tbl=_sql.Identifier(self.schema, table),
            where=where,
            order=_sql.SQL(", ").join(_sql.Identifier(c) for c in order_by),
        )
        if self.server_side:
            with self.conn.cursor(name=f"dbparity_{abs(hash((table, pk_range)))}") as cur:
                cur.itersize = batch
                cur.execute(q, params)
                for row in cur:
                    yield tuple(row)
        else:
            with self.conn.cursor() as cur:
                cur.execute(q, params)
                while True:
                    rows = cur.fetchmany(batch)
                    if not rows:
                        break
                    for row in rows:
                        yield tuple(row)

    # ---- digest-API ----------------------------------------------------------

    supports_digest = True

    def _ident(self, col: str) -> str:  # pragma: no cover
        return '"' + col.replace('"', '""') + '"'

    def _canon(self, col: str, logical: str, rtrim: bool) -> str:  # pragma: no cover
        q = self._ident(col)
        if logical == "number":
            # trim_scale (PG13+): 100.00 → '100', 1.50 → '1.5' — совпадает
            # с канонизацией sqlite/Oracle
            return (f"CASE WHEN {q} IS NULL THEN 'N' "
                    f"ELSE trim_scale({q}::numeric)::text END")
        if logical == "bool":
            return (f"CASE WHEN {q} IS NULL THEN 'N' "
                    f"WHEN {q} THEN '1' ELSE '0' END")
        v = f"rtrim({q}, ' ')" if rtrim else q
        return f"CASE WHEN {q} IS NULL THEN 'N' ELSE md5({v}) END"

    def _tbl(self) -> str:  # pragma: no cover
        return f'{self._ident(self.schema)}.'

    def pk_bounds(self, table: str, pk_col: str):  # pragma: no cover
        q = self._ident(pk_col)
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT MIN({q}), MAX({q}) FROM "
                        f"{self._tbl()}{self._ident(table)} WHERE {q} IS NOT NULL")
            row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)

    def null_pk_count(self, table: str, pk_col: str) -> int:  # pragma: no cover
        q = self._ident(pk_col)
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {self._tbl()}{self._ident(table)} "
                        f"WHERE {q} IS NULL")
            return int(cur.fetchone()[0])

    def bucket_digests(self, table: str, columns, logicals, pk_col: str,
                       lo, step: int, hi, rtrim: bool = False) -> dict:  # pragma: no cover
        parts = " || '|' || ".join(
            self._canon(c, lg, rtrim) for c, lg in zip(columns, logicals))
        q = self._ident(pk_col)
        sql_text = (
            f"SELECT b, COUNT(*), "
            f"COALESCE(SUM(('x' || substr(h, 1, 8))::bit(32)::bigint), 0), "
            f"COALESCE(SUM(('x' || substr(h, 9, 8))::bit(32)::bigint), 0), "
            f"COALESCE(SUM(('x' || substr(h, 17, 8))::bit(32)::bigint), 0) "
            f"FROM (SELECT floor(({q} - %s)::numeric / %s)::bigint AS b, "
            f"md5({parts}) AS h "
            f"FROM {self._tbl()}{self._ident(table)} "
            f"WHERE {q} >= %s AND {q} <= %s) sub GROUP BY b"
        )
        out = {}
        with self.conn.cursor() as cur:
            cur.execute(sql_text, (lo, step, lo, hi))
            for b, c, s1, s2, s3 in cur.fetchall():
                out[int(b)] = (int(c), int(s1), int(s2), int(s3))
        return out

    def close(self) -> None:  # pragma: no cover
        self.conn.close()
