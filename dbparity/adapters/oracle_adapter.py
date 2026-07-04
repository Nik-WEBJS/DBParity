"""Oracle adapter (python-oracledb, thin mode — no Instant Client)."""
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
        return "datetime"   # Oracle DATE carries time — compared as datetime
    if any(x in u for x in ("BLOB", "RAW")):
        return "bytes"
    return "text"


class OracleAdapter(Adapter):
    dialect = "oracle"
    binary_collation_supported = True   # ORDER BY NLSSORT(..., BINARY)

    def __init__(self, endpoint):
        if oracledb is None:  # pragma: no cover
            raise RuntimeError("oracle support requires: pip install oracledb")
        super().__init__(endpoint)
        # CRITICAL for a verifier:
        # 1) NUMBER arrives as float by default → precision loss on
        #    large/fractional values → false results. Fetch Decimal.
        # 2) LOB locators → values right away (str/bytes), otherwise
        #    comparison is impossible after the cursor closes.
        oracledb.defaults.fetch_decimals = True
        oracledb.defaults.fetch_lobs = False
        o = endpoint.options
        self.conn = oracledb.connect(
            user=o.get("user"), password=o.get("password"), dsn=o.get("dsn"),
        )
        self.owner = (o.get("schema") or o.get("user") or "").upper()

    def list_tables(self) -> List[str]:  # pragma: no cover — no server in CI
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
        pk_range=None, order_logicals: Sequence[str] | None = None,
    ) -> Iterator[tuple]:  # pragma: no cover
        def q(name: str) -> str:
            return '"' + name.replace('"', '""') + '"'

        where, params = "", {}
        if pk_range is not None:
            col, lo, hi = pk_range
            if hi is None:      # open range — for resume with a watermark
                where = f" WHERE {q(col)} >= :lo"
                params = {"lo": lo}
            else:
                where = f" WHERE {q(col)} >= :lo AND {q(col)} <= :hi"
                params = {"lo": lo, "hi": hi}
        # text order_by columns — binary sorting, independent of the
        # session's NLS_SORT (consistent with COLLATE "C"/BINARY of other DBs)
        logs = order_logicals or [None] * len(order_by)
        order_sql = ", ".join(
            f"NLSSORT({q(c)}, 'NLS_SORT=BINARY')" if lg == "text" else q(c)
            for c, lg in zip(order_by, logs))
        cur = self.conn.cursor()
        cur.arraysize = batch
        cur.execute(
            f'SELECT {", ".join(q(c) for c in columns)} '
            f'FROM {q(self.owner)}.{q(table.upper())}{where} '
            f'ORDER BY {order_sql}',
            params,
        )
        for row in cur:
            yield tuple(row)

    # ---- digest API (experimental: not exercised on a live Oracle) ----------

    supports_digest = True

    @staticmethod
    def _q(name: str) -> str:  # pragma: no cover
        return '"' + name.replace('"', '""') + '"'

    def _canon(self, col: str, logical: str, rtrim: bool) -> str:  # pragma: no cover
        q = self._q(col)
        if logical == "number":
            # TM9: 100 → '100', 1.5 → '1.5'. Caveat: 0.5 → '.5' (not '0.5') —
            # for such values the hash diverges from PG and the segment goes
            # into row mode: slower, but correct.
            return f"CASE WHEN {q} IS NULL THEN 'N' ELSE TO_CHAR({q}, 'TM9') END"
        if logical == "bool":
            return (f"CASE WHEN {q} IS NULL THEN 'N' "
                    f"WHEN {q} = 1 THEN '1' ELSE '0' END")
        v = f"RTRIM({q}, ' ')" if rtrim else q
        # In Oracle '' == NULL, so empty strings fall into the 'N' branch;
        # a divergence with the target sends the segment into row mode,
        # where the oracle_empty_string_is_null rule applies.
        return (f"CASE WHEN {q} IS NULL THEN 'N' "
                f"ELSE LOWER(RAWTOHEX(STANDARD_HASH({v}, 'MD5'))) END")

    def pk_bounds(self, table: str, pk_col: str):  # pragma: no cover
        cur = self.conn.cursor()
        cur.execute(
            f"SELECT MIN({self._q(pk_col)}), MAX({self._q(pk_col)}) "
            f"FROM {self._q(self.owner)}.{self._q(table.upper())} "
            f"WHERE {self._q(pk_col)} IS NOT NULL")
        row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)

    def null_pk_count(self, table: str, pk_col: str) -> int:  # pragma: no cover
        cur = self.conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) FROM {self._q(self.owner)}.{self._q(table.upper())} "
            f"WHERE {self._q(pk_col)} IS NULL")
        return int(cur.fetchone()[0])

    def bucket_digests(self, table: str, columns, logicals, pk_col: str,
                       lo, step: int, hi, rtrim: bool = False) -> dict:  # pragma: no cover
        parts = " || '|' || ".join(
            self._canon(c, lg, rtrim) for c, lg in zip(columns, logicals))
        q = self._q(pk_col)
        sql_text = (
            f"SELECT b, COUNT(*), "
            f"COALESCE(SUM(TO_NUMBER(SUBSTR(h, 1, 8), 'XXXXXXXX')), 0), "
            f"COALESCE(SUM(TO_NUMBER(SUBSTR(h, 9, 8), 'XXXXXXXX')), 0), "
            f"COALESCE(SUM(TO_NUMBER(SUBSTR(h, 17, 8), 'XXXXXXXX')), 0) "
            f"FROM (SELECT FLOOR(({q} - :lo1) / :st) AS b, "
            f"LOWER(RAWTOHEX(STANDARD_HASH({parts}, 'MD5'))) AS h "
            f"FROM {self._q(self.owner)}.{self._q(table.upper())} "
            f"WHERE {q} >= :lo2 AND {q} <= :hi) GROUP BY b"
        )
        cur = self.conn.cursor()
        cur.execute(sql_text, {"lo1": lo, "st": step, "lo2": lo, "hi": hi})
        return {int(b): (int(c), int(s1), int(s2), int(s3))
                for b, c, s1, s2, s3 in cur.fetchall()}

    def close(self) -> None:  # pragma: no cover
        self.conn.close()
