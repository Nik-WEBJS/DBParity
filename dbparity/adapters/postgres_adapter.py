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
    ) -> Iterator[tuple]:  # pragma: no cover
        q = _sql.SQL("SELECT {cols} FROM {tbl} ORDER BY {order}").format(
            cols=_sql.SQL(", ").join(_sql.Identifier(c) for c in columns),
            tbl=_sql.Identifier(self.schema, table),
            order=_sql.SQL(", ").join(_sql.Identifier(c) for c in order_by),
        )
        if self.server_side:
            with self.conn.cursor(name=f"dbparity_{abs(hash(table))}") as cur:
                cur.itersize = batch
                cur.execute(q)
                for row in cur:
                    yield tuple(row)
        else:
            with self.conn.cursor() as cur:
                cur.execute(q)
                while True:
                    rows = cur.fetchmany(batch)
                    if not rows:
                        break
                    for row in rows:
                        yield tuple(row)

    def close(self) -> None:  # pragma: no cover
        self.conn.close()
