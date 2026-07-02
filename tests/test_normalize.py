"""Юнит-тесты нормализации: каждая «ловушка миграции» из PLAN.md §3."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from dbparity.core.normalize import Normalizer, NormalizeRules


def n(rules=None, dialect="generic"):
    return Normalizer(rules, dialect)


def test_oracle_empty_string_is_null():
    assert n(dialect="oracle").normalize("") is None
    assert n(dialect="generic").normalize("") == ""


def test_none_stays_none():
    assert n().normalize(None) is None


def test_decimal_trailing_zeros():
    assert n().normalize(Decimal("1.50")) == n().normalize(Decimal("1.5"))


def test_int_float_decimal_cross_equal():
    nz = n()
    assert nz.normalize(1) == nz.normalize(1.0) == nz.normalize(Decimal("1.00"))


def test_float_epsilon():
    nz = n()
    assert nz.normalize(1.0 + 1e-12) == nz.normalize(1.0)
    assert nz.normalize(1.001) != nz.normalize(1.0)


def test_bool_vs_number():
    nz = n()
    assert nz.normalize(True) == nz.normalize(1)
    assert nz.normalize(False) == nz.normalize(0)


def test_yn_as_bool():
    nz = n(NormalizeRules(yn_as_bool=True))
    assert nz.normalize("Y") == nz.normalize(True)
    assert nz.normalize("n") == nz.normalize(False)
    # без правила Y остаётся строкой
    assert n().normalize("Y") == "Y"


def test_timezone_to_utc():
    nz = n()
    msk = timezone(timedelta(hours=3))
    a = nz.normalize(datetime(2025, 1, 1, 12, 0, tzinfo=msk))
    b = nz.normalize(datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc))
    assert a == b


def test_midnight_truncation_flag():
    with_flag = n(NormalizeRules(truncate_time_if_midnight=True))
    assert with_flag.normalize(datetime(2025, 1, 1, 0, 0)) == \
        with_flag.normalize(date(2025, 1, 1))
    without = n()
    assert without.normalize(datetime(2025, 1, 1, 0, 0)) != \
        without.normalize(date(2025, 1, 1))


def test_rtrim():
    nz = n(NormalizeRules(rtrim_strings=True))
    assert nz.normalize("abc   ") == "abc"
    assert n().normalize("abc   ") == "abc   "


def test_unicode_nfc():
    # е + комбинируемая точка сверху (NFD) == ё (NFC)
    nz = n()
    assert nz.normalize("ё") == nz.normalize("ё")


def test_bytes_md5():
    nz = n()
    assert nz.normalize(b"xx") == nz.normalize(bytearray(b"xx"))
    assert nz.normalize(b"xx") != nz.normalize(b"yy")


def test_timestamp_precision():
    nz = n(NormalizeRules(timestamp_precision=3))
    a = nz.normalize(datetime(2025, 1, 1, 1, 1, 1, 123456))
    b = nz.normalize(datetime(2025, 1, 1, 1, 1, 1, 123999))
    assert a == b
