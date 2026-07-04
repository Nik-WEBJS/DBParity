"""Unit tests for the merge comparison: every diff category."""
from dbparity.core.compare import compare_table
from dbparity.core.models import DiffKind
from dbparity.core.normalize import Normalizer

N = Normalizer()


def cmp(src, dst, **kw):
    return compare_table("t", ["id", "v"], ["id"], iter(src), iter(dst), N, N, **kw)


def test_equal_streams():
    r = cmp([(1, "a"), (2, "b")], [(1, "a"), (2, "b")])
    assert r.ok and r.matched == 2 and r.total_diffs == 0


def test_missing_in_target():
    r = cmp([(1, "a"), (2, "b"), (3, "c")], [(1, "a"), (3, "c")])
    assert r.missing_in_target == 1 and r.matched == 2
    assert r.samples[0].kind == DiffKind.MISSING_IN_TARGET
    assert r.samples[0].pk == ("2",)


def test_extra_in_target():
    r = cmp([(1, "a")], [(1, "a"), (2, "x")])
    assert r.extra_in_target == 1 and r.matched == 1


def test_mismatch_with_column_details():
    r = cmp([(1, "a")], [(1, "b")])
    assert r.mismatched == 1
    assert r.column_mismatch_counts == {"v": 1}
    assert r.samples[0].columns == {"v": ("a", "b")}


def test_duplicate_pk():
    r = cmp([(1, "a"), (1, "a2"), (2, "b")], [(1, "a"), (2, "b")])
    assert r.duplicate_pk == 1
    assert r.missing_in_target == 1   # the unpaired copy of the duplicate
    assert r.matched == 2


def test_row_counts():
    r = cmp([(1, "a"), (2, "b"), (3, "c")], [(1, "a")])
    assert r.src_rows == 3 and r.dst_rows == 1


def test_sample_limit():
    src = [(i, "a") for i in range(10)]
    dst = [(i, "b") for i in range(10)]
    r = cmp(src, dst, sample_limit=3)
    assert r.mismatched == 10 and len(r.samples) == 3


def test_mask_values():
    r = cmp([(1, "secret")], [(1, "other")], mask_values=True)
    assert r.samples[0].columns == {"v": ("•••", "•••")}
    # the PK is not masked
    assert r.samples[0].pk == ("1",)


def test_empty_both():
    r = cmp([], [])
    assert r.ok and r.src_rows == 0 and r.dst_rows == 0


def test_null_pk_isolated():
    """Rows with a NULL PK do not break the merge and get their own category."""
    r = cmp([(None, "x"), (1, "a")], [(1, "a"), (None, "y")])
    assert r.null_pk == 2
    assert r.matched == 1
    assert r.total_diffs == 2
    kinds = {s.kind for s in r.samples}
    assert DiffKind.NULL_PK in kinds


def test_fast_path_parity_with_generic():
    """Fast-path (per-column normalizers) yields the same result as generic."""
    src = [(1, "a", 1.5, ""), (2, "b", 2.0, "x"), (4, "d", 4.0, None)]
    dst = [(1, "a", 1.5, ""), (2, "B", 2.0000000001, "x"), (3, "c", 3.0, "y")]
    cols = ["id", "name", "price", "note"]
    logicals = ["number", "text", "float", "text"]

    slow = compare_table("t", cols, ["id"], iter(src), iter(dst), N, N)
    fast = compare_table("t", cols, ["id"], iter(src), iter(dst), N, N,
                         src_logicals=logicals, dst_logicals=logicals)
    for attr in ("matched", "mismatched", "missing_in_target",
                 "extra_in_target", "duplicate_pk", "column_mismatch_counts",
                 "src_rows", "dst_rows"):
        assert getattr(slow, attr) == getattr(fast, attr), attr
    # sanity: epsilon applied in the fast-path too (price is not flagged)
    assert "price" not in fast.column_mismatch_counts
    assert fast.column_mismatch_counts == {"name": 1}
