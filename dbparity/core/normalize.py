"""Value normalization: reduction to a canonical form before comparison.

The classic "migration traps" are encoded here (see PLAN.md §3):
Oracle ''==NULL, trailing NUMBER zeros, float epsilon, time zones,
CHAR padding, Unicode normalization, boolean mappings, BLOB→MD5.
"""
from __future__ import annotations

import hashlib
import math
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Optional


@dataclass
class NormalizeRules:
    oracle_empty_string_is_null: bool = True
    rtrim_strings: bool = False
    unicode_nfc: bool = True
    float_epsilon: float = 1e-9
    yn_as_bool: bool = False
    truncate_time_if_midnight: bool = False
    timestamp_precision: int = 6        # microsecond digits (0..6)
    tz_to_utc: bool = True
    bytes_as_md5: bool = True


_YES = {"y", "t"}
_YN = {"y", "n", "t", "f"}


class Normalizer:
    def __init__(self, rules: Optional[NormalizeRules] = None, dialect: str = "generic"):
        self.rules = rules or NormalizeRules()
        self.dialect = dialect
        eps = self.rules.float_epsilon
        self._float_digits = max(0, round(-math.log10(eps))) if eps > 0 else None

    def normalize(self, value: Any) -> Any:
        r = self.rules
        if value is None:
            return None
        if isinstance(value, bool):
            return Decimal(1) if value else Decimal(0)
        if isinstance(value, int):
            return Decimal(value)
        if isinstance(value, float):
            if self._float_digits is not None:
                value = round(value, self._float_digits)
            return Decimal(repr(value))
        if isinstance(value, Decimal):
            return value
        if isinstance(value, datetime):
            if r.tz_to_utc and value.tzinfo is not None:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            if r.timestamp_precision < 6:
                factor = 10 ** (6 - r.timestamp_precision)
                value = value.replace(microsecond=(value.microsecond // factor) * factor)
            if r.truncate_time_if_midnight and value.time() == time(0, 0):
                return value.date()
            return value
        if isinstance(value, date):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            b = bytes(value)
            if r.bytes_as_md5:
                return "md5:" + hashlib.md5(b).hexdigest()
            return b
        if isinstance(value, str):
            v = value
            if r.unicode_nfc:
                v = unicodedata.normalize("NFC", v)
            if r.rtrim_strings:
                v = v.rstrip(" ")
            if (self.dialect == "oracle" and r.oracle_empty_string_is_null
                    and v == ""):
                return None
            if r.yn_as_bool and v.lower() in _YN:
                return Decimal(1) if v.lower() in _YES else Decimal(0)
            return v
        return value

    # ---- fast path: precompiled per-column normalizers ---------------------

    def row_normalizer(self, logicals=None):
        """Returns a row→tuple function.

        If the logical column types are known (from the adapter schema), a
        narrow function without the isinstance-check chain is compiled for
        each column; on an unexpected value type — fall back to the generic
        normalize().
        """
        if not logicals:
            gen = self.normalize
            return lambda row: tuple(map(gen, row))
        funcs = [self._compile(lg) for lg in logicals]
        def norm_row(row):
            return tuple(f(v) for f, v in zip(funcs, row))
        return norm_row

    def _compile(self, logical: str):
        r = self.rules
        generic = self.normalize

        if logical == "number":
            def f_num(v):
                t = type(v)
                if t is int:
                    return Decimal(v)
                if t is Decimal or v is None:
                    return v
                return generic(v)
            return f_num

        if logical == "float":
            digits = self._float_digits
            def f_float(v):
                if type(v) is float:
                    if digits is not None:
                        v = round(v, digits)
                    return Decimal(repr(v))
                if v is None:
                    return None
                return generic(v)
            return f_float

        if logical == "text":
            nfc = r.unicode_nfc
            rtrim = r.rtrim_strings
            empty_null = (self.dialect == "oracle"
                          and r.oracle_empty_string_is_null)
            yn = r.yn_as_bool
            _nfc = unicodedata.normalize
            def f_text(v):
                if type(v) is str:
                    if nfc:
                        v = _nfc("NFC", v)
                    if rtrim:
                        v = v.rstrip(" ")
                    if empty_null and v == "":
                        return None
                    if yn and v.lower() in _YN:
                        return Decimal(1) if v.lower() in _YES else Decimal(0)
                    return v
                if v is None:
                    return None
                return generic(v)
            return f_text

        # datetime/date/bytes/bool and the rest — the generic path
        return generic
