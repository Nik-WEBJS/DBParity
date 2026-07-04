"""Table structure comparison: columns, logical types, primary keys.

Name matching is case-insensitive (Oracle returns UPPER, Postgres — lower).
Types are compared by the adapter's "logical" type (text/number/float/datetime/…),
not by the engines' raw type names.
"""
from __future__ import annotations

from typing import Dict

from ..adapters.base import TableSchema
from .models import TableSchemaDiff


def diff_table_schema(table: str, src: TableSchema, dst: TableSchema) -> TableSchemaDiff:
    d = TableSchemaDiff(table=table)
    src_cols = {c.name.lower(): c for c in src.columns}
    dst_cols = {c.name.lower(): c for c in dst.columns}

    d.missing_in_target = [c for c in src_cols if c not in dst_cols]
    d.extra_in_target = [c for c in dst_cols if c not in src_cols]

    for name in src_cols:
        if name in dst_cols and src_cols[name].logical != dst_cols[name].logical:
            d.type_changes.append({
                "column": name,
                "source": f"{src_cols[name].raw} ({src_cols[name].logical})",
                "target": f"{dst_cols[name].raw} ({dst_cols[name].logical})",
            })

    src_pk = [p.lower() for p in src.pk]
    dst_pk = [p.lower() for p in dst.pk]
    if src_pk != dst_pk:
        d.pk_mismatch = {"source": src_pk, "target": dst_pk}
    return d


def diff_schemas(
    src_schemas: Dict[str, TableSchema],
    dst_schemas: Dict[str, TableSchema],
) -> list:
    """Returns only tables that have structural differences."""
    out = []
    for table in sorted(src_schemas):
        if table in dst_schemas:
            d = diff_table_schema(table, src_schemas[table], dst_schemas[table])
            if d.has_diffs:
                out.append(d)
    return out
