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
    ) -> Iterator[tuple]:
        """Строки в порядке ORDER BY <order_by>, чанками по batch."""
        ...

    def close(self) -> None:  # noqa: B027 — переопределяется при необходимости
        pass
