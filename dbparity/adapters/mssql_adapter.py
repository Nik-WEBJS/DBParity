"""MSSQL-адаптер (pyodbc). Каркас: требует установленный ODBC-драйвер."""
from __future__ import annotations

from typing import Iterator, List, Sequence

from .base import Adapter, ColumnSchema, TableSchema

try:
    import pyodbc
except ImportError:  # pragma: no cover
    pyodbc = None


def _logical(data_type: str) -> str:
    u = (data_type or "").lower()
    if u == "bit":
        return "bool"
    if any(x in u for x in ("int", "numeric", "decimal", "money")):
        return "number"
    if any(x in u for x in ("float", "real")):
        return "float"
    if "date" in u or "time" in u:
        return "datetime"
    if any(x in u for x in ("binary", "image")):
        return "bytes"
    return "text"


class MSSQLAdapter(Adapter):
    dialect = "mssql"

    def __init__(self, endpoint):
        if pyodbc is None:  # pragma: no cover
            raise RuntimeError(
                "Для mssql установите pyodbc и системный ODBC-драйвер "
                "(msodbcsql18), либо используйте другой тип источника."
            )
        super().__init__(endpoint)
        self.conn = pyodbc.connect(endpoint.options.get("dsn"))
        self.schema = endpoint.options.get("schema", "dbo")

    def list_tables(self) -> List[str]:  # pragma: no cover
        cur = self.conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = ? AND table_type = 'BASE TABLE' ORDER BY table_name",
            self.schema,
        )
        return [r[0] for r in cur.fetchall()]

    def table_schema(self, table: str) -> TableSchema:  # pragma: no cover
        cur = self.conn.cursor()
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            self.schema, table,
        )
        cols = [ColumnSchema(name=r[0], logical=_logical(r[1]), raw=r[1])
                for r in cur.fetchall()]
        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = tc.constraint_name
             AND kcu.table_schema = tc.table_schema
            WHERE tc.table_schema = ? AND tc.table_name = ?
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            """,
            self.schema, table,
        )
        pk = [r[0] for r in cur.fetchall()]
        return TableSchema(name=table, columns=cols, pk=pk)

    def stream_rows(
        self, table: str, columns: Sequence[str],
        order_by: Sequence[str], batch: int,
        pk_range=None,
    ) -> Iterator[tuple]:  # pragma: no cover
        def q(name: str) -> str:
            return "[" + name.replace("]", "]]") + "]"

        where, params = "", ()
        if pk_range is not None:
            col, lo, hi = pk_range
            where = f" WHERE {q(col)} >= ? AND {q(col)} <= ?"
            params = (lo, hi)
        cur = self.conn.cursor()
        cur.execute(
            f'SELECT {", ".join(q(c) for c in columns)} '
            f'FROM {q(self.schema)}.{q(table)}{where} '
            f'ORDER BY {", ".join(q(c) for c in order_by)}',
            *params,
        )
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            for row in rows:
                yield tuple(row)

    def close(self) -> None:  # pragma: no cover
        self.conn.close()
