"""Comparison result models.

The JSON report schema version (REPORT_SCHEMA_VERSION) also lives here —
the report format is frozen and described in docs/report-format.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

#: JSON report schema version (the "schema_version" key in RunResult.to_dict()).
#:
#: Format evolution rules (semver guarantees, v0.9 roadmap):
#: - ADDING new keys is a minor change: the version does NOT change,
#:   consumers must ignore unfamiliar keys. Every new key must be
#:   documented in docs/report-format.md.
#: - REMOVING or RENAMING a key, or changing a value's type/semantics, is
#:   a major change: REPORT_SCHEMA_VERSION is incremented and the change
#:   is described in docs/report-format.md and the CHANGELOG.
#: The frozen v1 key set is guarded by tests/test_report_schema.py.
REPORT_SCHEMA_VERSION = 1


class DiffKind(str, Enum):
    MISSING_IN_TARGET = "missing_in_target"   # the row exists in source but not in target
    EXTRA_IN_TARGET = "extra_in_target"       # an extra row in target
    MISMATCH = "mismatch"                     # PK matched, values differ
    DUPLICATE_PK = "duplicate_pk"             # duplicate primary key
    NULL_PK = "null_pk"                       # NULL in PK — merging on such a row is impossible


@dataclass
class RowDiff:
    kind: DiffKind
    pk: tuple
    # for MISMATCH: {column: (source_value, target_value)}
    columns: Optional[dict] = None


@dataclass
class TableResult:
    table: str
    pk: list
    src_rows: int = 0
    dst_rows: int = 0
    matched: int = 0
    mismatched: int = 0
    missing_in_target: int = 0
    extra_in_target: int = 0
    duplicate_pk: int = 0
    null_pk: int = 0
    samples: list = field(default_factory=list)
    column_mismatch_counts: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    error: Optional[str] = None
    duration_s: float = 0.0
    mode: str = "stream"                # stream | hash
    rows_hash_matched: int = 0          # rows credited via matching segments
    rows_streamed: int = 0              # rows detailed via streaming (src+dst)
    segments_matched: int = 0
    segments_streamed: int = 0

    @property
    def total_diffs(self) -> int:
        return (self.mismatched + self.missing_in_target
                + self.extra_in_target + self.duplicate_pk + self.null_pk)

    @property
    def ok(self) -> bool:
        return self.error is None and self.total_diffs == 0

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        return "ok" if self.total_diffs == 0 else "diff"

    @property
    def match_pct(self) -> float:
        base = max(self.src_rows, self.dst_rows)
        if base == 0:
            return 100.0
        return round(self.matched / base * 100, 4)


@dataclass
class TableSchemaDiff:
    table: str
    missing_in_target: list = field(default_factory=list)   # columns
    extra_in_target: list = field(default_factory=list)     # columns
    type_changes: list = field(default_factory=list)        # [{column, source, target}]
    pk_mismatch: Optional[dict] = None                      # {source: [...], target: [...]}

    @property
    def has_diffs(self) -> bool:
        return bool(self.missing_in_target or self.extra_in_target
                    or self.type_changes or self.pk_mismatch)


@dataclass
class RunResult:
    source_label: str
    target_label: str
    started_at: datetime
    finished_at: datetime
    tables: list = field(default_factory=list)              # [TableResult]
    schema_diffs: list = field(default_factory=list)        # [TableSchemaDiff]
    tables_only_in_source: list = field(default_factory=list)
    tables_only_in_target: list = field(default_factory=list)
    config_summary: dict = field(default_factory=dict)

    @property
    def schema_clean(self) -> bool:
        return (not self.schema_diffs and not self.tables_only_in_source
                and not self.tables_only_in_target)

    @property
    def equivalent(self) -> bool:
        return self.schema_clean and all(t.ok for t in self.tables)

    @property
    def totals(self) -> dict:
        t = {
            "tables_total": len(self.tables),
            "tables_ok": sum(1 for x in self.tables if x.ok),
            "src_rows": sum(x.src_rows for x in self.tables),
            "dst_rows": sum(x.dst_rows for x in self.tables),
            "matched": sum(x.matched for x in self.tables),
            "mismatched": sum(x.mismatched for x in self.tables),
            "missing_in_target": sum(x.missing_in_target for x in self.tables),
            "extra_in_target": sum(x.extra_in_target for x in self.tables),
            "duplicate_pk": sum(x.duplicate_pk for x in self.tables),
            "null_pk": sum(x.null_pk for x in self.tables),
        }
        t["total_diffs"] = (t["mismatched"] + t["missing_in_target"]
                            + t["extra_in_target"] + t["duplicate_pk"]
                            + t["null_pk"])
        base = max(t["src_rows"], t["dst_rows"])
        t["match_pct"] = round(t["matched"] / base * 100, 4) if base else 100.0
        return t

    def to_dict(self) -> dict:
        """Dict for the JSON report. The format is frozen: docs/report-format.md.

        The first meaningful field is "schema_version" — consumers check it
        before parsing the rest. Key evolution rules are in the docstring of
        the REPORT_SCHEMA_VERSION constant above.
        """
        d: dict = {"schema_version": REPORT_SCHEMA_VERSION}
        d.update(asdict(self))
        d["equivalent"] = self.equivalent
        d["totals"] = self.totals
        for tr, src in zip(d["tables"], self.tables):
            tr["total_diffs"] = src.total_diffs
            tr["status"] = src.status
            tr["match_pct"] = src.match_pct
        return d
