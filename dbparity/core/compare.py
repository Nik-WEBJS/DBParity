"""Стриминговое сравнение двух упорядоченных по PK потоков строк.

Merge-алгоритм: O(n) по времени, O(batch) по памяти. Потоки обязаны быть
отсортированы по первичному ключу на стороне БД (ORDER BY).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from .models import DiffKind, RowDiff, TableResult
from .normalize import Normalizer

_SENTINEL = object()


def _lt(a: Sequence[Any], b: Sequence[Any]) -> bool:
    """Поэлементное < для PK-кортежей; при разнотипье — фолбэк на строки."""
    for x, y in zip(a, b):
        if x == y:
            continue
        try:
            return x < y
        except TypeError:
            return str(x) < str(y)
    return len(a) < len(b)


def _eq(a: Any, b: Any) -> bool:
    try:
        return a == b
    except Exception:
        return str(a) == str(b)


def _display(value: Any, mask: bool, limit: int = 120) -> Any:
    if mask:
        return "•••"
    if value is None:
        return None
    s = str(value)
    return s[: limit - 1] + "…" if len(s) > limit else s


class _Stream:
    """Обёртка над потоком строк: нормализация, PK, детект дублей, счётчик."""

    def __init__(self, rows: Iterable[tuple], norm_row, pk_idx: Sequence[int]):
        self._it = iter(rows)
        self._norm_row = norm_row
        self._pk_idx = pk_idx
        self.raw: Any = None
        self.n: Optional[tuple] = None
        self.pk: Optional[tuple] = None
        self.prev: Optional[tuple] = None   # последний потреблённый PK
        self.dup = False
        self.count = 0
        self.advance()

    @property
    def exhausted(self) -> bool:
        return self.raw is _SENTINEL

    def advance(self) -> None:
        prev_pk = self.pk
        self.prev = prev_pk
        raw = next(self._it, _SENTINEL)
        if raw is _SENTINEL:
            self.raw, self.n, self.pk, self.dup = _SENTINEL, None, None, False
            return
        self.count += 1
        self.raw = raw
        self.n = self._norm_row(raw)
        self.pk = tuple(self.n[i] for i in self._pk_idx)
        self.dup = prev_pk is not None and self.pk == prev_pk


def compare_table(
    table: str,
    columns: Sequence[str],
    pk_columns: Sequence[str],
    src_rows: Iterable[tuple],
    dst_rows: Iterable[tuple],
    norm_src: Normalizer,
    norm_dst: Normalizer,
    sample_limit: int = 50,
    mask_values: bool = False,
    src_logicals: Optional[Sequence[str]] = None,
    dst_logicals: Optional[Sequence[str]] = None,
    progress=None,
    initial: Optional[TableResult] = None,
    checkpoint=None,
    checkpoint_every: int = 0,
    track_max_idx: Optional[int] = None,
) -> TableResult:
    # initial — восстановленный частичный результат (resume): счётчики
    # продолжают накапливаться поверх него.
    # track_max_idx — индекс watermark-колонки в columns (инкрементальный
    # режим): во время merge отслеживается максимум НОРМАЛИЗОВАННЫХ значений
    # этой колонки по обеим сторонам. Результат возвращается ДИНАМИЧЕСКИМ
    # атрибутом res.max_tracked (models.py не расширяем; в dataclasses.asdict
    # и чекпоинты атрибут не попадает). None — если строк не было.
    res = initial if initial is not None else TableResult(
        table=table, pk=list(pk_columns))
    pk_idx = [list(columns).index(c) for c in pk_columns]
    val_idx = [i for i in range(len(columns)) if i not in pk_idx]

    def add_sample(kind: DiffKind, row: tuple, cols: Optional[dict] = None) -> None:
        if len(res.samples) < sample_limit:
            pk_disp = tuple(_display(row[i], mask=False) for i in pk_idx)
            res.samples.append(RowDiff(kind=kind, pk=pk_disp, columns=cols))

    def note_dup(stream: _Stream) -> None:
        if not stream.exhausted and stream.dup:
            res.duplicate_pk += 1
            add_sample(DiffKind.DUPLICATE_PK, stream.raw)

    S = _Stream(src_rows, norm_src.row_normalizer(src_logicals), pk_idx)
    D = _Stream(dst_rows, norm_dst.row_normalizer(dst_logicals), pk_idx)
    last_report = 0
    last_ckpt = 0
    base_src, base_dst = res.src_rows, res.dst_rows   # база из initial (resume)

    # res-независимый максимум watermark-колонки (см. док у track_max_idx)
    max_tracked = None

    def track(stream: _Stream) -> None:
        """Учитывает текущую строку потока в максимуме watermark-колонки."""
        nonlocal max_tracked
        if stream.exhausted:
            return
        v = stream.n[track_max_idx]
        if v is None:
            return
        if max_tracked is None:
            max_tracked = v
            return
        try:                            # разнотипье — фолбэк на строки (как _lt)
            greater = max_tracked < v
        except TypeError:
            greater = str(max_tracked) < str(v)
        if greater:
            max_tracked = v

    while not (S.exhausted and D.exhausted):
        if track_max_idx is not None:
            # каждая потреблённая строка хотя бы раз «текущая» в начале
            # итерации; повторный учёт той же строки максимум не меняет
            track(S)
            track(D)
        if progress is not None:
            n = S.count + D.count
            if n - last_report >= 20_000:
                progress(n)
                last_report = n
        if checkpoint is not None and checkpoint_every > 0:
            n = S.count + D.count
            if n - last_ckpt >= checkpoint_every:
                # фронтир мержа: всё с pk < wm уже учтено
                if S.exhausted:
                    wm = D.pk
                elif D.exhausted:
                    wm = S.pk
                else:
                    wm = S.pk if _lt(S.pk, D.pk) else D.pk
                # дубликат на границе: wm совпадает с уже потреблённым PK —
                # безопасной точки нет, откладываем чекпоинт
                if wm is not None and wm != S.prev and wm != D.prev:
                    # буферная (вытянутая, но не обработанная) строка каждой
                    # стороны будет прочитана заново при resume — не считаем
                    res.src_rows = base_src + S.count - (0 if S.exhausted else 1)
                    res.dst_rows = base_dst + D.count - (0 if D.exhausted else 1)
                    checkpoint(res, wm[0])
                    last_ckpt = n
        # NULL в PK: merge по такой строке невозможен — отдельная категория
        if not S.exhausted and None in S.pk:
            res.null_pk += 1
            add_sample(DiffKind.NULL_PK, S.raw)
            S.advance()
            continue
        if not D.exhausted and None in D.pk:
            res.null_pk += 1
            add_sample(DiffKind.NULL_PK, D.raw)
            D.advance()
            continue
        if D.exhausted or (not S.exhausted and _lt(S.pk, D.pk)):
            res.missing_in_target += 1
            add_sample(DiffKind.MISSING_IN_TARGET, S.raw)
            S.advance()
            note_dup(S)
        elif S.exhausted or _lt(D.pk, S.pk):
            res.extra_in_target += 1
            add_sample(DiffKind.EXTRA_IN_TARGET, D.raw)
            D.advance()
            note_dup(D)
        else:
            diff_cols = {}
            for i in val_idx:
                if not _eq(S.n[i], D.n[i]):
                    col = columns[i]
                    diff_cols[col] = (
                        _display(S.raw[i], mask_values),
                        _display(D.raw[i], mask_values),
                    )
                    res.column_mismatch_counts[col] = (
                        res.column_mismatch_counts.get(col, 0) + 1
                    )
            if diff_cols:
                res.mismatched += 1
                add_sample(DiffKind.MISMATCH, S.raw, diff_cols)
            else:
                res.matched += 1
            S.advance()
            note_dup(S)
            D.advance()
            note_dup(D)

    if progress is not None:
        progress(S.count + D.count)
    res.src_rows = base_src + S.count
    res.dst_rows = base_dst + D.count
    if track_max_idx is not None:
        res.max_tracked = max_tracked   # динамический атрибут (см. выше)
    return res
