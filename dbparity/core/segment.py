"""Hash mode: bucketed DB-side aggregates in a single scan.

Idea: both databases compute aggregates of canonical md5 hashes over PK
buckets in ONE pass (GROUP BY floor((pk-lo)/step)). Matching buckets are
declared equivalent without transferring rows; diverging ones are detailed
via streaming merge with full client-side normalization. Result: 1 hash
scan per side + transfer of only the "suspicious" ranges.

Correctness property: imperfect canonicalization of equivalent values only
leads to extra detailing (slower), but never to a false pass. A false pass
would require an md5 aggregate collision.
"""
from __future__ import annotations

from .compare import compare_table
from .models import TableResult
from .normalize import Normalizer

_HASHABLE_LOGICALS = {"number", "text", "bool"}


def digest_eligible(config, src, dst, pk, common_cols,
                    src_log, dst_log) -> tuple:
    """(eligible: bool, reason: str)."""
    if config.strategy == "stream":
        return False, "strategy=stream"
    if not (src.supports_digest and dst.supports_digest):
        return False, "adapter lacks digest API"
    if len(pk) != 1:
        return False, "composite PK"
    p = pk[0]
    if src_log.get(p) != "number" or dst_log.get(p) != "number":
        return False, "non-numeric PK"
    bad = [c for c in common_cols
           if src_log.get(c) not in _HASHABLE_LOGICALS
           or dst_log.get(c) not in _HASHABLE_LOGICALS]
    if bad:
        return False, f"column types outside the hash set: {', '.join(bad[:4])}"
    return True, ""


def _merge(master: TableResult, part: TableResult, sample_limit: int) -> None:
    master.src_rows += part.src_rows
    master.dst_rows += part.dst_rows
    master.matched += part.matched
    master.mismatched += part.mismatched
    master.missing_in_target += part.missing_in_target
    master.extra_in_target += part.extra_in_target
    master.duplicate_pk += part.duplicate_pk
    master.null_pk += part.null_pk
    for col, cnt in part.column_mismatch_counts.items():
        master.column_mismatch_counts[col] = (
            master.column_mismatch_counts.get(col, 0) + cnt)
    room = sample_limit - len(master.samples)
    if room > 0:
        master.samples.extend(part.samples[:room])


def hash_compare_table(
    table: str,
    src, dst,
    src_table: str, dst_table: str,
    common_cols, pk_col: str,
    src_names: dict, dst_names: dict,
    src_logicals, dst_logicals,
    norm_src: Normalizer, norm_dst: Normalizer,
    config,
    progress=None,
) -> TableResult:
    res = TableResult(table=table, pk=[pk_col])
    res.mode = "hash"
    rtrim = config.rules.rtrim_strings

    src_cols_actual = [src_names[c] for c in common_cols]
    dst_cols_actual = [dst_names[c] for c in common_cols]
    src_pk_actual, dst_pk_actual = src_names[pk_col], dst_names[pk_col]

    # NULL in PK: WHERE pk >= lo excludes them from segments — count separately
    res.null_pk = (src.null_pk_count(src_table, src_pk_actual)
                   + dst.null_pk_count(dst_table, dst_pk_actual))

    lo_s, hi_s = src.pk_bounds(src_table, src_pk_actual)
    lo_d, hi_d = dst.pk_bounds(dst_table, dst_pk_actual)
    bounds = [b for b in (lo_s, lo_d) if b is not None]
    tops = [b for b in (hi_s, hi_d) if b is not None]
    if not bounds:          # both sides are empty (apart from possible NULL PKs)
        return res

    def report() -> None:
        if progress is not None:
            progress(res.src_rows + res.dst_rows)

    lo, hi = min(bounds), max(tops)
    step = max(1, int(config.hash_leaf_rows))

    src_b = src.bucket_digests(src_table, src_cols_actual, src_logicals,
                               src_pk_actual, lo, step, hi, rtrim=rtrim)
    dst_b = dst.bucket_digests(dst_table, dst_cols_actual, dst_logicals,
                               dst_pk_actual, lo, step, hi, rtrim=rtrim)

    for k in sorted(set(src_b) | set(dst_b)):
        a, b = src_b.get(k), dst_b.get(k)
        if a is not None and a == b:
            res.matched += a[0]
            res.src_rows += a[0]
            res.dst_rows += b[0]
            res.rows_hash_matched += a[0]
            res.segments_matched += 1
        else:
            seg_lo = lo + k * step
            seg_hi = seg_lo + step - 1
            if seg_hi > hi:
                seg_hi = hi
            part = compare_table(
                table, common_cols, [pk_col],
                src.stream_rows(src_table, src_cols_actual, [src_pk_actual],
                                config.batch_size,
                                pk_range=(src_pk_actual, seg_lo, seg_hi)),
                dst.stream_rows(dst_table, dst_cols_actual, [dst_pk_actual],
                                config.batch_size,
                                pk_range=(dst_pk_actual, seg_lo, seg_hi)),
                norm_src, norm_dst,
                sample_limit=max(0, config.sample_limit - len(res.samples)),
                mask_values=config.mask_values,
                src_logicals=src_logicals,
                dst_logicals=dst_logicals,
            )
            _merge(res, part, config.sample_limit)
            res.rows_streamed += part.src_rows + part.dst_rows
            res.segments_streamed += 1
        report()
    return res
