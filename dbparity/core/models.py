"""Модели результатов сверки.

Здесь же живёт версия схемы JSON-отчёта (REPORT_SCHEMA_VERSION) —
формат отчёта заморожен и описан в docs/report-format.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

#: Версия схемы JSON-отчёта (ключ "schema_version" в RunResult.to_dict()).
#:
#: Правила эволюции формата (semver-гарантии, роадмап v0.9):
#: - ДОБАВЛЕНИЕ новых ключей — минорное изменение: версия НЕ меняется,
#:   потребители обязаны игнорировать незнакомые ключи. Каждый новый ключ
#:   обязан быть задокументирован в docs/report-format.md.
#: - УДАЛЕНИЕ, ПЕРЕИМЕНОВАНИЕ ключа либо смена типа/семантики значения —
#:   мажорное изменение: REPORT_SCHEMA_VERSION инкрементируется, изменение
#:   описывается в docs/report-format.md и CHANGELOG.
#: Замороженный набор ключей v1 охраняется тестом tests/test_report_schema.py.
REPORT_SCHEMA_VERSION = 1


class DiffKind(str, Enum):
    MISSING_IN_TARGET = "missing_in_target"   # строка есть в источнике, нет в приёмнике
    EXTRA_IN_TARGET = "extra_in_target"       # лишняя строка в приёмнике
    MISMATCH = "mismatch"                     # PK совпал, значения различаются
    DUPLICATE_PK = "duplicate_pk"             # дубликат первичного ключа
    NULL_PK = "null_pk"                       # NULL в PK — merge по такой строке невозможен


@dataclass
class RowDiff:
    kind: DiffKind
    pk: tuple
    # для MISMATCH: {колонка: (значение_источника, значение_приёмника)}
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
    rows_hash_matched: int = 0          # строк зачтено по совпавшим сегментам
    rows_streamed: int = 0              # строк детализировано потоково (src+dst)
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
    missing_in_target: list = field(default_factory=list)   # колонки
    extra_in_target: list = field(default_factory=list)     # колонки
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
        """Словарь для JSON-отчёта. Формат заморожен: docs/report-format.md.

        Первым смысловым полем идёт "schema_version" — потребители проверяют
        его до разбора остального. Правила эволюции ключей — в докстринге
        константы REPORT_SCHEMA_VERSION выше.
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
