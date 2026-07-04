"""Data source adapters."""
from __future__ import annotations

from .base import Adapter, ColumnSchema, TableSchema


def build_adapter(endpoint) -> Adapter:
    """Adapter factory based on the endpoint config."""
    t = endpoint.type
    if t == "sqlite":
        from .sqlite_adapter import SQLiteAdapter
        return SQLiteAdapter(endpoint)
    if t in ("postgres", "postgresql"):
        from .postgres_adapter import PostgresAdapter
        return PostgresAdapter(endpoint)
    if t == "oracle":
        from .oracle_adapter import OracleAdapter
        return OracleAdapter(endpoint)
    if t == "mssql":
        from .mssql_adapter import MSSQLAdapter
        return MSSQLAdapter(endpoint)
    raise ValueError(f"Unknown source type: {t!r} "
                     f"(supported: sqlite, postgres, oracle, mssql)")


__all__ = ["Adapter", "ColumnSchema", "TableSchema", "build_adapter"]
