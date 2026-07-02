"""Оркестратор: схемы → сверка таблиц (параллельно, с retry и resume) → RunResult."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..adapters import build_adapter
from ..config import Config
from .checkpoint import Checkpointer, config_fingerprint
from .compare import compare_table
from .models import RunResult, TableResult
from .normalize import Normalizer
from .schema_diff import diff_schemas
from .segment import digest_eligible, hash_compare_table


def _compare_one(src, dst, config: Config, t: str, src_name: str, dst_name: str,
                 ss, ds, norm_src: Normalizer, norm_dst: Normalizer,
                 on_progress, resume_snapshot=None, checkpoint_cb=None) -> TableResult:
    """Сверка одной таблицы. Ошибки БД ПРОБРАСЫВАЮТСЯ (retry решает вызывающий)."""
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
        # Текстовые PK: порядок merge зависит от коллаций движков (Oracle
        # BINARY vs PG locale). Если оба адаптера умеют бинарную сортировку —
        # навязываем её обеим сторонам через order_logicals.
        pk_text = [p for p in pk
                   if src_log.get(p) == "text" or dst_log.get(p) == "text"]
        binary_sort = bool(pk_text) \
            and getattr(src, "binary_collation_supported", False) \
            and getattr(dst, "binary_collation_supported", False)
        progress = (lambda n: on_progress(t, n)) if on_progress else None
        eligible, why = digest_eligible(
            config, src, dst, pk, common_cols, src_log, dst_log)
        if eligible:
            tr = hash_compare_table(
                t, src, dst, src_name, dst_name, common_cols, pk[0],
                src_names, dst_names,
                [src_log[c] for c in common_cols],
                [dst_log[c] for c in common_cols],
                norm_src, norm_dst, config, progress)
        else:
            initial = start_from = None
            if resume_snapshot is not None and len(pk) == 1:
                initial, start_from = resume_snapshot
                initial.warnings.append(
                    "Продолжено с чекпоинта (watermark PK "
                    f"{start_from!r})")
            src_range = ((src_names[pk[0]], start_from, None)
                         if start_from is not None else None)
            dst_range = ((dst_names[pk[0]], start_from, None)
                         if start_from is not None else None)
            ckpt_kw = {}
            if checkpoint_cb is not None and len(pk) == 1:
                ckpt_kw = {"checkpoint": checkpoint_cb,
                           "checkpoint_every": config.checkpoint_every_rows}
            # order_logicals передаём только при текстовом PK: для числовых
            # PK он ничего не меняет, а обёртки stream_rows со старой
            # сигнатурой (тесты, сторонние адаптеры) остаются совместимыми
            src_bin = ({"order_logicals": [src_log[p] for p in pk]}
                       if binary_sort else {})
            dst_bin = ({"order_logicals": [dst_log[p] for p in pk]}
                       if binary_sort else {})
            src_stream = src.stream_rows(
                src_name, [src_names[c] for c in common_cols],
                [src_names[p] for p in pk], config.batch_size,
                pk_range=src_range, **src_bin)
            dst_stream = dst.stream_rows(
                dst_name, [dst_names[c] for c in common_cols],
                [dst_names[p] for p in pk], config.batch_size,
                pk_range=dst_range, **dst_bin)
            tr = compare_table(
                t, common_cols, pk, src_stream, dst_stream,
                norm_src, norm_dst,
                sample_limit=config.sample_limit,
                mask_values=config.mask_values,
                src_logicals=[src_log[c] for c in common_cols],
                dst_logicals=[dst_log[c] for c in common_cols],
                progress=progress,
                initial=initial,
                **ckpt_kw,
            )
        if config.strategy == "hash" and not eligible:
            tr.warnings.append(
                f"hash-режим недоступен ({why}) — использована потоковая сверка")
        if pk_text and binary_sort:
            tr.warnings.append(
                f"PK содержит текстовые колонки ({', '.join(pk_text)}): "
                "применена бинарная сортировка на обеих сторонах — "
                "порядок merge не зависит от коллаций движков")
        elif pk_text:
            tr.warnings.append(
                f"PK содержит текстовые колонки ({', '.join(pk_text)}): "
                "порядок сортировки может различаться между движками "
                "из-за коллаций. Проверьте COLLATE/NLS_SORT или "
                "используйте числовой PK.")
    tr.duration_s = round(time.perf_counter() - t0, 3)
    return tr


def run(config: Config, on_progress=None, resume: bool = False) -> RunResult:
    """Выполняет сверку.

    on_progress(table, rows_done) — колбэк прогресса (из потоков при workers>1).
    resume=True — продолжить с чекпоинта (файл из config.checkpoint либо
    авто-имя .dbparity_ckpt_<fp>.json).
    """
    started = datetime.now(timezone.utc)
    src = build_adapter(config.source)
    dst = build_adapter(config.target)
    src_label, dst_label = src.label, dst.label

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
                    error="Таблица отсутствует в источнике и/или приёмнике"))
        common = [t for t in wanted if t in common]

    src_schemas = {t: src.table_schema(src_tables[t]) for t in common}
    dst_schemas = {t: dst.table_schema(dst_tables[t]) for t in common}
    schema_diffs = diff_schemas(src_schemas, dst_schemas)

    # схемная фаза окончена: каждая таблица получит свои соединения,
    # держать эти дальше незачем (и вредно для однопоточных окружений)
    for a in (src, dst):
        try:
            a.close()
        except Exception:  # noqa: BLE001
            pass

    norm_src = Normalizer(config.rules, dialect=src.dialect)
    norm_dst = Normalizer(config.rules, dialect=dst.dialect)

    ckpt = None
    if config.checkpoint or resume:
        fp = config_fingerprint(config)
        path = config.checkpoint or f".dbparity_ckpt_{fp[:12]}.json"
        ckpt = Checkpointer.load_or_create(path, fp, resume)

    def job(t: str) -> TableResult:
        if ckpt is not None:
            done = ckpt.done_table(t)
            if done is not None:
                done.warnings.append(
                    "Восстановлена из чекпоинта — сверка не повторялась")
                return done
        attempts = max(1, config.retry_attempts)
        last_err = None
        for attempt in range(1, attempts + 1):
            s2 = d2 = None
            try:
                s2 = build_adapter(config.source)
                d2 = build_adapter(config.target)
                snap = cb = None
                if ckpt is not None:      # partial-слоты потокобезопасны
                    snap = ckpt.current_snapshot(t)
                    cb = (lambda tr, wm, _t=t: ckpt.snapshot(_t, tr, wm))
                tr = _compare_one(s2, d2, config, t, src_tables[t],
                                  dst_tables[t], src_schemas[t],
                                  dst_schemas[t], norm_src, norm_dst,
                                  on_progress,
                                  resume_snapshot=snap, checkpoint_cb=cb)
                if ckpt is not None:
                    ckpt.table_done(tr)
                return tr
            except Exception as e:  # noqa: BLE001 — сетевые/БД ошибки → retry
                last_err = e
                if attempt < attempts:
                    time.sleep(config.retry_backoff_s * attempt)
            finally:
                for a in (s2, d2):
                    try:
                        if a is not None:
                            a.close()
                    except Exception:  # noqa: BLE001
                        pass
        return TableResult(
            table=t, pk=[],
            error=f"{type(last_err).__name__}: {last_err} "
                  f"(после {attempts} попыт.)")

    if config.workers > 1 and len(common) > 1:
        with ThreadPoolExecutor(max_workers=config.workers) as ex:
            futures = {t: ex.submit(job, t) for t in common}
            results.extend(futures[t].result() for t in common)
    else:
        results.extend(job(t) for t in common)

    if ckpt is not None:
        ckpt.finish(delete=all(t.error is None for t in results))

    return RunResult(
        source_label=src_label,
        target_label=dst_label,
        started_at=started,
        finished_at=datetime.now(timezone.utc),
        tables=results,
        schema_diffs=schema_diffs,
        tables_only_in_source=[src_tables[t] for t in only_src],
        tables_only_in_target=[dst_tables[t] for t in only_dst],
        config_summary=config.summary(),
    )
