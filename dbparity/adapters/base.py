"""Base database adapter interface."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Iterator, List, Sequence


@dataclass
class ColumnSchema:
    name: str
    logical: str          # text | number | float | bool | datetime | date | bytes
    raw: str = ""         # the engine's original type name


@dataclass
class TableSchema:
    name: str
    columns: List[ColumnSchema] = field(default_factory=list)
    pk: List[str] = field(default_factory=list)


class Adapter(abc.ABC):
    """Dialect-independent contract: the core sees only this interface."""

    dialect = "generic"

    # Whether the adapter can sort text ORDER BY columns in binary
    # collation (see order_logicals in stream_rows). All built-in adapters
    # can; a third-party adapter without support must set False —
    # then the engine leaves a collation warning instead of a guarantee.
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
        """Rows in ORDER BY <order_by> order, in chunks of batch.

        pk_range=(col, lo, hi) — an optional filter lo <= col <= hi
        (used by hash mode to detail segments).

        order_logicals — an optional list of logical types of the order_by
        columns (a parallel list). If given, TEXT columns are sorted in the
        engine's binary collation (COLLATE BINARY / "C" /
        NLSSORT BINARY / Latin1_General_BIN2) — so the merge-comparison
        order matches across engines regardless of their default collations.
        None — the previous behavior (the engine's default sorting).
        """
        ...

    # ---- digest API for segment hashes (hash mode) ---------------------------
    # Contract: the canonical representation must be INJECTIVE per column
    # (different values → different strings). Imperfect canonicalization of
    # equivalent values only causes a descent into row mode (slower), but
    # not a false skip.

    supports_digest = False

    def pk_bounds(self, table: str, pk_col: str):
        """(min, max) of PK values without NULLs; (None, None) if the table is empty."""
        raise NotImplementedError

    def null_pk_count(self, table: str, pk_col: str) -> int:
        raise NotImplementedError

    def bucket_digests(self, table: str, columns, logicals, pk_col: str,
                       lo, step: int, hi, rtrim: bool = False) -> dict:
        """Aggregates of canonical strings over PK buckets in ONE scan.

        bucket = floor((pk - lo) / step); returns
        {bucket: (count, s1, s2, s3)} for PK in [lo, hi].
        """
        raise NotImplementedError

    def close(self) -> None:  # noqa: B027 — overridden when necessary
        pass
