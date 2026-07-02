"""Базовый интерфейс адаптера БД."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Iterator, List, Sequence


@dataclass
class ColumnSchema:
    name: str
    logical: str          # text | number | float | bool | datetime | date | bytes
    raw: str = ""         # исходное имя типа движка


@dataclass
class TableSchema:
    name: str
    columns: List[ColumnSchema] = field(default_factory=list)
    pk: List[str] = field(default_factory=list)


class Adapter(abc.ABC):
    """Диалект-независимый контракт: ядро видит только этот интерфейс."""

    dialect = "generic"

    # Умеет ли адаптер сортировать текстовые ORDER BY-колонки в бинарной
    # коллации (см. order_logicals в stream_rows). Все встроенные адаптеры
    # умеют; сторонний адаптер без поддержки обязан выставить False —
    # тогда движок оставит предупреждение о коллациях вместо гарантии.
    binary_collation_supported = True

    def __init__(self, endpoint):
        self.endpoint = endpoint

    @property
    def label(self) -> str:
        return self.endpoint.label or f"{self.endpoint.type}"

    @abc.abstractmethod
    def list_tables(self) -> List[str]: ...

    @abc.abstractmethod
    def table_schema(self, table: str) -> TableSchema: ...

    @abc.abstractmethod
    def stream_rows(
        self, table: str, columns: Sequence[str],
        order_by: Sequence[str], batch: int,
        pk_range=None, order_logicals: Sequence[str] | None = None,
    ) -> Iterator[tuple]:
        """Строки в порядке ORDER BY <order_by>, чанками по batch.

        pk_range=(col, lo, hi) — необязательный фильтр lo <= col <= hi
        (используется hash-режимом для детализации сегментов).

        order_logicals — необязательный список логических типов колонок
        order_by (параллельный список). Если задан, ТЕКСТОВЫЕ колонки
        сортируются в бинарной коллации движка (COLLATE BINARY / "C" /
        NLSSORT BINARY / Latin1_General_BIN2) — так порядок merge-сравнения
        совпадает между движками независимо от их коллаций по умолчанию.
        None — прежнее поведение (сортировка по умолчанию движка).
        """
        ...

    # ---- digest-API для сегментных хэшей (hash-режим) -----------------------
    # Контракт: canonical-представление обязано быть ИНЪЕКТИВНЫМ по колонке
    # (разные значения → разные строки). Неидеальная канонизация эквивалентных
    # значений лишь вызывает спуск в row-режим (медленнее), но не ложный skip.

    supports_digest = False

    def pk_bounds(self, table: str, pk_col: str):
        """(min, max) значений PK без NULL; (None, None) если таблица пуста."""
        raise NotImplementedError

    def null_pk_count(self, table: str, pk_col: str) -> int:
        raise NotImplementedError

    def bucket_digests(self, table: str, columns, logicals, pk_col: str,
                       lo, step: int, hi, rtrim: bool = False) -> dict:
        """Агрегаты канонических строк по бакетам PK за ОДИН скан.

        bucket = floor((pk - lo) / step); возвращает
        {bucket: (count, s1, s2, s3)} для PK в [lo, hi].
        """
        raise NotImplementedError

    def close(self) -> None:  # noqa: B027 — переопределяется при необходимости
        pass
