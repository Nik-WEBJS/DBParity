"""MSSQL adapter (pyodbc): connection, streaming, and the digest API.

Requires the pyodbc package and a system ODBC driver (msodbcsql18).
Connection — either a ready-made ODBC string (options.dsn) or the
host/port/database/user/password parts (the adapter assembles the string).
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Sequence

from .base import Adapter, ColumnSchema, TableSchema

try:
    import pyodbc
    # The ODBC pool reuses sessions (and their context: current DB, SET
    # options) between "new" connections with the same string. For a
    # verifier such hidden shared state is unacceptable — every adapter
    # must get a genuinely fresh session.
    pyodbc.pooling = False
except ImportError:  # pragma: no cover
    pyodbc = None

_DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"

# The ODBC type code of datetimeoffset (SQL_SS_TIMESTAMPOFFSET): pyodbc
# does not know this proprietary type and, without a converter, fails with
# "ODBC SQL type -155 is not yet supported" on the very first fetch.
_SQL_SS_TIMESTAMPOFFSET = -155


def _decode_datetimeoffset(raw: bytes) -> datetime:  # pragma: no cover
    """The binary datetimeoffset structure → an aware datetime.

    SQL Server format: year..second (6 × int16), fractional seconds in
    nanoseconds (uint32), timezone offset hours/minutes (2 × int16,
    both signed: -05:30 arrives as (-5, -30)). Nanoseconds are
    truncated to Python microseconds. The normalizer then converts
    aware values to naive UTC (the tz_to_utc rule) — comparison with
    other engines is correct.
    """
    y, mo, d, h, mi, s, ns, oh, om = struct.unpack("<6hI2h", raw)
    return datetime(y, mo, d, h, mi, s, ns // 1000,
                    timezone(timedelta(hours=oh, minutes=om)))


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
    binary_collation_supported = True   # ORDER BY ... COLLATE Latin1_General_BIN2

    def __init__(self, endpoint):
        if pyodbc is None:  # pragma: no cover
            raise RuntimeError(
                "mssql support requires pyodbc and a system ODBC driver "
                "(msodbcsql18), or use a different source type."
            )
        super().__init__(endpoint)
        o = endpoint.options
        dsn = o.get("dsn")
        if not dsn:  # pragma: no cover — string assembly is covered by the unit logic below
            dsn = self._build_dsn(o)
        # autocommit=True: the verifier only reads and does not need
        # transactions, while autocommit=False on SQL Server opens an
        # implicit transaction that holds shared locks for the whole
        # duration of the stream and (per CI observations) causes object
        # visibility anomalies on fresh sessions. Overridable with the
        # option autocommit: false.
        self.conn = pyodbc.connect(dsn, autocommit=bool(o.get("autocommit", True)))
        self.conn.add_output_converter(_SQL_SS_TIMESTAMPOFFSET,
                                       _decode_datetimeoffset)
        self.schema = o.get("schema", "dbo")

    @staticmethod
    def _build_dsn(o: dict) -> str:
        """An ODBC string from the host/port/database/user/password parts.

        By default Driver 18 requires a verifiable server certificate —
        for a typical verify run we enable TrustServerCertificate=yes
        (encryption stays, CA verification is disabled).
        """
        driver = o.get("driver", _DEFAULT_DRIVER)
        parts = [
            f"Driver={{{driver}}}",
            f"Server={o.get('host', 'localhost')},{o.get('port', 1433)}",
        ]
        database = o.get("database") or o.get("dbname")
        if database:
            parts.append(f"Database={database}")
        if o.get("user"):
            parts.append(f"UID={o['user']}")
        if o.get("password") is not None:
            parts.append(f"PWD={o['password']}")
        parts.append("TrustServerCertificate=yes")
        return ";".join(parts)

    @staticmethod
    def _q(name: str) -> str:
        return "[" + name.replace("]", "]]") + "]"

    def _tbl(self, table: str) -> str:
        return f"{self._q(self.schema)}.{self._q(table)}"

    def list_tables(self) -> List[str]:  # pragma: no cover — no server in unit CI
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
        pk_range=None, order_logicals: Sequence[str] | None = None,
    ) -> Iterator[tuple]:  # pragma: no cover
        q = self._q
        where, params = "", ()
        if pk_range is not None:
            col, lo, hi = pk_range
            if hi is None:      # open range — for resume with a watermark
                where = f" WHERE {q(col)} >= ?"
                params = (lo,)
            else:
                where = f" WHERE {q(col)} >= ? AND {q(col)} <= ?"
                params = (lo, hi)
        # text order_by columns — binary collation instead of the DB collation
        logs = order_logicals or [None] * len(order_by)
        order_sql = ", ".join(
            f"{q(c)} COLLATE Latin1_General_BIN2" if lg == "text" else q(c)
            for c, lg in zip(order_by, logs))
        cur = self.conn.cursor()
        cur.arraysize = batch
        cur.execute(
            f'SELECT {", ".join(q(c) for c in columns)} '
            f'FROM {self._tbl(table)}{where} '
            f'ORDER BY {order_sql}',
            *params,
        )
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            for row in rows:
                yield tuple(row)

    # ---- digest API (exercised by the live test tests/test_mssql_integration.py)

    supports_digest = True

    @staticmethod
    def _md5(expr: str) -> str:
        """Lowercase md5 hex: CONVERT style 2 — hex without the 0x prefix."""
        return f"LOWER(CONVERT(VARCHAR(32), HASHBYTES('MD5', {expr}), 2))"

    def _canon(self, col: str, logical: str, rtrim: bool) -> str:
        """The column's canonical string representation (a T-SQL expression).

        Contract (see base.py): per-column injectivity is mandatory;
        a canonicalization divergence between engines merely sends the
        segment into row mode (slower, but correct — see the
        core/segment.py docstring).
        """
        q = self._q(col)
        if logical == "number":
            # Reduce to the trim_scale (PG) / sqlite form: 100.00 → '100',
            # 1.50 → '1.5', 0.5 → '0.5'. T-SQL has no TRIM(TRAILING ...),
            # so: CAST to DECIMAL(38,10) yields a string with exactly 10
            # fractional digits (the dot is always present), then we cut
            # the trailing zeros.
            # PATINDEX('%[^0]%', REVERSE(v)) — the position of the first
            # "non-zero" from the end; a value of 11 means "the whole
            # fraction is zero" (the 11th character from the end is the
            # dot itself) — then we cut 11 characters ('.0000000000'),
            # otherwise only the zeros (PATINDEX - 1).
            #
            # Trade-offs (they do not produce false matches BETWEEN engines):
            # scale > 10 is rounded by the CAST — for such columns in an
            # MSSQL↔MSSQL comparison trust strategy=stream; more than 28
            # integer digits — an arithmetic overflow (an explicit query
            # error, not a silent skip).
            v = f"CONVERT(VARCHAR(50), CAST({q} AS DECIMAL(38, 10)))"
            trimmed = (
                f"LEFT({v}, LEN({v}) - CASE "
                f"WHEN PATINDEX('%[^0]%', REVERSE({v})) = 11 THEN 11 "
                f"ELSE PATINDEX('%[^0]%', REVERSE({v})) - 1 END)"
            )
            return f"CASE WHEN {q} IS NULL THEN 'N' ELSE {trimmed} END"
        if logical == "bool":
            return (f"CASE WHEN {q} IS NULL THEN 'N' "
                    f"WHEN {q} = 1 THEN '1' ELSE '0' END")
        v = f"RTRIM({q})" if rtrim else q
        # CAST to VARCHAR(MAX): for ASCII the bytes match UTF-8, i.e. the
        # md5 equals md5()/md5hex() of the other engines and the buckets
        # match. Non-ASCII in NVARCHAR may degenerate into '?' (the DB code
        # page) — the digest diverges from the source and the segment goes
        # into row mode with an honest client-side comparison: slower, but
        # correct.
        return (f"CASE WHEN {q} IS NULL THEN 'N' "
                f"ELSE {self._md5(f'CAST({v} AS VARCHAR(MAX))')} END")

    def pk_bounds(self, table: str, pk_col: str):  # pragma: no cover
        q = self._q(pk_col)
        cur = self.conn.cursor()
        cur.execute(f"SELECT MIN({q}), MAX({q}) FROM {self._tbl(table)} "
                    f"WHERE {q} IS NOT NULL")
        row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)

    def null_pk_count(self, table: str, pk_col: str) -> int:  # pragma: no cover
        q = self._q(pk_col)
        cur = self.conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {self._tbl(table)} "
                    f"WHERE {q} IS NULL")
        return int(cur.fetchone()[0])

    def bucket_digests(self, table: str, columns, logicals, pk_col: str,
                       lo, step: int, hi, rtrim: bool = False) -> dict:  # pragma: no cover
        # Concatenation in T-SQL is the + operator; the canon branches never
        # return NULL ('N'), so the whole string is never NULL.
        parts = " + '|' + ".join(
            self._canon(c, lg, rtrim) for c, lg in zip(columns, logicals))
        q = self._q(pk_col)

        def word(pos: int) -> str:
            # 8 hex characters → VARBINARY(4) (style 1 understands the 0x
            # prefix) → BIGINT: an unsigned 32-bit word, like
            # ('x'||h)::bit(32) in PG and hex2int in sqlite.
            return ("CONVERT(BIGINT, CONVERT(VARBINARY(4), "
                    f"'0x' + SUBSTRING(h, {pos}, 8), 1))")

        # (pk - lo) >= 0 due to the WHERE, so T-SQL integer division
        # (truncation toward zero) is equivalent to FLOOR; FLOOR is kept
        # for decimal PKs, where the division yields a fraction.
        sql_text = (
            f"SELECT b, COUNT(*), "
            f"COALESCE(SUM({word(1)}), 0), "
            f"COALESCE(SUM({word(9)}), 0), "
            f"COALESCE(SUM({word(17)}), 0) "
            f"FROM (SELECT FLOOR(({q} - ?) / ?) AS b, "
            f"{self._md5(parts)} AS h "
            f"FROM {self._tbl(table)} "
            f"WHERE {q} >= ? AND {q} <= ?) AS sub GROUP BY b"
        )
        cur = self.conn.cursor()
        cur.execute(sql_text, lo, step, lo, hi)
        return {int(b): (int(c), int(s1), int(s2), int(s3))
                for b, c, s1, s2, s3 in cur.fetchall()}

    def close(self) -> None:  # pragma: no cover
        self.conn.close()
