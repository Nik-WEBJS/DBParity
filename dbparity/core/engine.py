"""Оркестратор прогона: схемы → сверка таблиц (опц. параллельно) → RunResult."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..adapters import build_adapter
from ..config import Config
from .compare import compare_table
from .models import RunResult, TableResult
from .normalize import Normalizer
from .schema_diff import diff_schemas


def _compare_one(src, dst, config: Config, t: str, src_name: str, dst_name: str,
                 ss, ds, norm_src: Normalizer, norm_dst: Normalizer,
                 on_progress) -> TableResult:
    """Сверка одной таблицы через переданные адаптеры."""
    t0 = time.perf_counter()
    src_names = {c.name.lower(): c.name for c in ss.columns}
    dst_names = {c.name.lower(): c.name for c in ds.columns}
    excluded = set(config.exclude_columns.get(t, []))
    common_cols = [c.name.lower() for c in ss.columns
                   if c.name.lower() in dst_names
                   and c.name.lower() not in excluded]
    pk = config.pk_overrides.get(t) or [p.lower() for p in ss.pk]

    if not pk:
        tr = TableResult(table=t, pk=[], error=(
            "Не удалось определить первичный ключ — задайте pk_overrides"))
    elif any(p not in common_cols for p in pk):
        tr = TableResult(table=t, pk=pk, error=(
            "PK-колонки отсутствуют в общем наборе колонок обеих таблиц"))
    else:
        src_log = {c.name.lower(): c.logical for c in ss.columns}
        dst_log = {c.name.lower(): c.logical for c in ds.columns}
        progress = (lambda n: on_progress(t, n)) if on_progress else None
        src_stream = src.stream_rows(
            src_name, [src_names[c] for c in common_cols],
            [src_names[p] for p in pk], config.batch_size)
        dst_stream = dst.stream_rows(
            dst_name, [dst_names[c] for c in common_cols],
            [dst_names[p] for p in pk], config.batch_size)
        try:
            tr = compare_table(
                t, common_cols, pk, src_stream, dst_stream,
                norm_src, norm_dst,
                sample_limit=config.sample_limit,
                mask_values=config.mask_values,
                src_logicals=[src_log[c] for c in common_cols],
                dst_logicals=[dst_log[c] for c in common_cols],
                progress=progress,
            )
        except Exception as e:  # noqa: BLE001 — ошибки уходят в отчёт
            tr = TableResult(table=t, pk=pk, error=f"{type(e).__name__}: {e}")
        pk_text = [p for p in pk
                   if src_log.get(p) == "text" or dst_log.get(p) == "text"]
        if pk_text:
            tr.warnings.append(
                f"PK содержит текстовые колонки ({', '.join(pk_text)}): "
                "порядок сортировки может различаться между движками "
                "из-за коллаций. Проверьте COLLATE/NLS_SORT или "
                "используйте числовой PK.")
    tr.duration_s = round(time.perf_counter() - t0, 3)
    return tr


def run(config: Config, on_progress=None) -> RunResult:
    """Выполняет сверку.

    on_progress(table: str, rows_done: int) — необязательный колбэк прогресса
    (вызывается из рабочих потоков при workers > 1).
    """
    started = datetime.now(timezone.utc)
    src = build_adapter(config.source)
    dst = build_adapter(config.target)
    try:
        src_tables = {t.lower(): t for t in src.list_tables()}
        dst_tables = {t.lower(): t for t in dst.list_tables()}
        only_src = sorted(set(src_tables) - set(dst_tables))
        only_dst = sorted(set(dst_tables) - set(src_tables))
        common = sorted(set(src_tables) & set(dst_tables))

        results = []
        if config.tables:
            wanted = [t.lower() for t in config.tables]
            for t in wanted:
                if t not in common:
                    results.append(TableResult(
                        table=t, pk=[],
                        error="Таблица отсутствует в источнике и/или приёмнике",
                    ))
            common = [t for t in wanted if t in common]

        src_schemas = {t: src.table_schema(src_tables[t]) for t in common}
        dst_schemas = {t: dst.table_schema(dst_tables[t]) for t in common}
        schema_diffs = diff_schemas(src_schemas, dst_schemas)

        norm_src = Normalizer(config.rules, dialect=src.dialect)
        norm_dst = Normalizer(config.rules, dialect=dst.dialect)

        def job(t: str) -> TableResult:
            if config.workers > 1:
                # соединение на поток: адаптеры не разделяются между потоками
                s2, d2 = build_adapter(config.source), build_adapter(config.target)
                try:
                    return _compare_one(s2, d2, config, t, src_tables[t],
                                        dst_tables[t], src_schemas[t],
                                        dst_schemas[t], norm_src, norm_dst,
                                        on_progress)
                finally:
                    s2.close()
                    d2.close()
            return _compare_one(src, dst, config, t, src_tables[t],
                                dst_tables[t], src_schemas[t], dst_schemas[t],
                                norm_src, norm_dst, on_progress)

        if config.workers > 1 and len(common) > 1:
            with ThreadPoolExecutor(max_workers=config.workers) as ex:
                futures = {t: ex.submit(job, t) for t in common}
                results.extend(futures[t].result() for t in common)
        else:
            results.extend(job(t) for t in common)

        return RunResult(
            source_label=src.label,
            target_label=dst.label,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            tables=results,
            schema_diffs=schema_diffs,
            tables_only_in_source=[src_tables[t] for t in only_src],
            tables_only_in_target=[dst_tables[t] for t in only_dst],
            config_summary=config.summary(),
        )
    finally:
        src.close()
        dst.close()
