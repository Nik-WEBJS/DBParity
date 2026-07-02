"""Oracle-адаптер (python-oracledb, thin mode — без Instant Client)."""
from __future__ import annotations

from typing import Iterator, List, Sequence

from .base import Adapter, ColumnSchema, TableSchema

try:
    import oracledb
except ImportError:  # pragma: no cover
    oracledb = None


def _logical(data_type: str) -> str:
    u = (data_type or "").upper()
    if any(x in u for x in ("VARCHAR", "CHAR", "CLOB", "LONG")):
        return "text"
    if u == "NUMBER":
        return "number"
    if any(x in u for x in ("BINARY_FLOAT", "BINARY_DOUBLE", "FLOAT")):
        return "float"
    if "TIMESTAMP" in u or u == "DATE":
        return "datetime"   # Oracle DATE несёт время — сравнивается как datetime
    if any(x in u for x in ("BLOB", "RAW")):
        return "bytes"
    return "text"


class OracleAdapter(Adapter):
    dialect = "oracle"

    def __init__(self, endpoint):
        if oracledb is None:  # pragma: no cover
            raise RuntimeError("Для oracle установите зависимость: pip install oracledb")
        super().__init__(endpoint)
        o = endpoint.options
        self.conn = oracledb.connect(
            user=o.get("user"), password=o.get("password"), dsn=o.get("dsn"),
        )
        self.owner = (o.get("schema") or o.get("user") or "").upper()

    def list_tables(self) -> List[str]:  # pragma: no cover — нет сервера в CI
        cur = self.conn.cursor()
        cur.execute(
            "SELECT table_name FROM all_tables WHERE owner = :o ORDER BY table_name",
            o=self.owner,
        )
        return [r[0] for r in cur.fetchall()]

    def table_schema(self, table: str) -> TableSchema:  # pragma: no cover
        cur = self.conn.cursor()
        cur.execute(
            "SELECT column_name, data_type FROM all_tab_columns "
            "WHERE owner = :o AND table_name = :t ORDER BY column_id",
            o=self.owner, t=table.upper(),
        )
        cols = [ColumnSchema(name=n, logical=_logical(t), raw=t)
                for n, t in cur.fetchall()]
        cur.execute(
            """
            SELECT cc.column_name
            FROM all_constraints c
            JOIN all_cons_columns cc
              ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name
            WHERE c.owner = :o AND c.table_name = :t AND c.constraint_type = 'P'
            ORDER BY cc.position
            """,
            o=self.owner, t=table.upper(),
        )
        pk = [r[0] for r in cur.fetchall()]
        return TableSchema(name=table, columns=cols, pk=pk)

    def stream_rows(
        self, table: str, columns: Sequence[str],
        order_by: Sequence[str], batch: int,
    ) -> Iterator[tuple]:  # pragma: no cover
        def q(name: str) -> str:
            return '"' + name.replace('"', '""') + '"'

        cur = self.conn.cursor()
        cur.arraysize = batch
        cur.execute(
            f'SELECT {", ".join(q(c) for c in columns)} '
            f'FROM {q(self.owner)}.{q(table.upper())} '
            f'ORDER BY {", ".join(q(c) for c in order_by)}'
        )
        for row in cur:
            yield tuple(row)

    def close(self) -> None:  # pragma: no cover
        self.conn.close()
