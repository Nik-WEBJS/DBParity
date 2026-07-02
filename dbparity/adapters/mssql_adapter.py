"""MSSQL-адаптер (pyodbc): подключение, стриминг и digest-API.

Требует пакет pyodbc и системный ODBC-драйвер (msodbcsql18).
Подключение — либо готовой ODBC-строкой (options.dsn), либо по частям
host/port/database/user/password (строка собирается адаптером).
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Sequence

from .base import Adapter, ColumnSchema, TableSchema

try:
    import pyodbc
except ImportError:  # pragma: no cover
    pyodbc = None

_DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"

# ODBC-код типа datetimeoffset (SQL_SS_TIMESTAMPOFFSET): pyodbc не знает
# этот проприетарный тип и без конвертера падает с "ODBC SQL type -155
# is not yet supported" на первом же fetch.
_SQL_SS_TIMESTAMPOFFSET = -155


def _decode_datetimeoffset(raw: bytes) -> datetime:  # pragma: no cover
    """Бинарная структура datetimeoffset → aware datetime.

    Формат SQL Server: год..секунда (6 × int16), доли секунды в
    наносекундах (uint32), смещение таймзоны часы/минуты (2 × int16,
    оба со знаком: -05:30 приходит как (-5, -30)). Наносекунды
    усекаются до микросекунд Python. Дальше нормализатор приводит
    aware-значения к naive UTC (правило tz_to_utc) — сравнение с
    другими движками корректно.
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
                "Для mssql установите pyodbc и системный ODBC-драйвер "
                "(msodbcsql18), либо используйте другой тип источника."
            )
        super().__init__(endpoint)
        o = endpoint.options
        dsn = o.get("dsn")
        if not dsn:  # pragma: no cover — сборка строки покрыта юнит-логикой ниже
            dsn = self._build_dsn(o)
        # autocommit по умолчанию False: сверка read-only, поведение
        # единообразно с остальными адаптерами; при необходимости
        # переопределяется опцией autocommit: true.
        self.conn = pyodbc.connect(dsn, autocommit=bool(o.get("autocommit", False)))
        self.conn.add_output_converter(_SQL_SS_TIMESTAMPOFFSET,
                                       _decode_datetimeoffset)
        self.schema = o.get("schema", "dbo")

    @staticmethod
    def _build_dsn(o: dict) -> str:
        """ODBC-строка из частей host/port/database/user/password.

        По умолчанию Driver 18 требует проверяемый сертификат сервера —
        для типового verify-прогона включаем TrustServerCertificate=yes
        (шифрование остаётся, проверка CA отключается).
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

    def list_tables(self) -> List[str]:  # pragma: no cover — нет сервера в CI юнитов
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
            if hi is None:      # открытый диапазон — для resume с watermark
                where = f" WHERE {q(col)} >= ?"
                params = (lo,)
            else:
                where = f" WHERE {q(col)} >= ? AND {q(col)} <= ?"
                params = (lo, hi)
        # текстовые order_by-колонки — бинарная коллация вместо коллации БД
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

    # ---- digest-API (обкатывается live-тестом tests/test_mssql_integration.py)

    supports_digest = True

    @staticmethod
    def _md5(expr: str) -> str:
        """md5-hex в нижнем регистре: CONVERT стиль 2 — hex без префикса 0x."""
        return f"LOWER(CONVERT(VARCHAR(32), HASHBYTES('MD5', {expr}), 2))"

    def _canon(self, col: str, logical: str, rtrim: bool) -> str:
        """Каноническое строковое представление колонки (T-SQL выражение).

        Контракт (см. base.py): инъективность по колонке обязательна;
        расхождение канонизаций между движками лишь уводит сегмент в
        row-режим (медленнее, но корректно — см. docstring core/segment.py).
        """
        q = self._q(col)
        if logical == "number":
            # Приводим к виду trim_scale (PG) / sqlite: 100.00 → '100',
            # 1.50 → '1.5', 0.5 → '0.5'. TRIM(TRAILING ...) в T-SQL нет,
            # поэтому: CAST к DECIMAL(38,10) даёт строку с ровно 10 знаками
            # дроби (точка есть всегда), затем срезаем хвостовые нули.
            # PATINDEX('%[^0]%', REVERSE(v)) — позиция первого «не-нуля»
            # с конца; значение 11 означает «вся дробь нулевая» (11-й
            # символ с конца — сама точка) — тогда срезаем 11 символов
            # ('.0000000000'), иначе только нули (PATINDEX - 1).
            #
            # Компромиссы (не дают ложных совпадений МЕЖДУ движками):
            # scale > 10 округляется CAST'ом — такие колонки при сверке
            # MSSQL↔MSSQL доверяйте strategy=stream; больше 28 целых
            # разрядов — арифметическое переполнение (явная ошибка
            # запроса, не тихий пропуск).
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
        # CAST в VARCHAR(MAX): для ASCII байты совпадают с UTF-8, то есть
        # md5 равен md5()/md5hex() других движков и бакеты совпадают.
        # Не-ASCII в NVARCHAR может выродиться в '?' (кодовая страница БД) —
        # digest разойдётся с источником и сегмент уйдёт в row-режим с
        # честным клиентским сравнением: медленнее, но корректно.
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
        # Конкатенация в T-SQL — оператор +; canon-ветки NULL не возвращают
        # ('N'), поэтому вся строка никогда не NULL.
        parts = " + '|' + ".join(
            self._canon(c, lg, rtrim) for c, lg in zip(columns, logicals))
        q = self._q(pk_col)

        def word(pos: int) -> str:
            # 8 hex-символов → VARBINARY(4) (стиль 1 понимает префикс 0x)
            # → BIGINT: беззнаковое 32-битное слово, как ('x'||h)::bit(32)
            # в PG и hex2int в sqlite.
            return ("CONVERT(BIGINT, CONVERT(VARBINARY(4), "
                    f"'0x' + SUBSTRING(h, {pos}, 8), 1))")

        # (pk - lo) >= 0 из-за WHERE, поэтому целочисленное деление T-SQL
        # (усечение к нулю) эквивалентно FLOOR; FLOOR оставлен для
        # decimal-PK, где деление даёт дробь.
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
