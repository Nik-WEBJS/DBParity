"""Loading and validation of the comparison YAML config."""
from __future__ import annotations

import dataclasses
import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .core.normalize import NormalizeRules

_SENSITIVE_KEYS = {"password", "passwd", "secret", "token"}


@dataclass
class EndpointConfig:
    type: str
    label: Optional[str] = None
    options: dict = field(default_factory=dict)


@dataclass
class ReportConfig:
    html: Optional[str] = None
    json: Optional[str] = None


@dataclass
class Config:
    source: EndpointConfig
    target: EndpointConfig
    tables: Optional[list] = None
    pk_overrides: dict = field(default_factory=dict)
    exclude_columns: dict = field(default_factory=dict)
    rules: NormalizeRules = field(default_factory=NormalizeRules)
    sample_limit: int = 50
    batch_size: int = 5000
    mask_values: bool = False
    workers: int = 1
    strategy: str = "auto"              # auto | stream | hash
    hash_leaf_rows: int = 20000         # PK bucket step (≈ rows per segment
                                        # for a dense key)
    checkpoint: Optional[str] = None    # checkpoint file path (enables resume)
    checkpoint_every_rows: int = 500000
    # Incremental mode: {table: watermark column}. The column exists in both
    # databases and grows monotonically when a row changes (timestamp/version);
    # the next run re-checks only rows with wm_col >= watermark.
    incremental: dict = field(default_factory=dict)
    retry_attempts: int = 1             # 1 = no retries
    retry_backoff_s: float = 2.0
    report: ReportConfig = field(default_factory=ReportConfig)

    def summary(self) -> dict:
        def safe(ep: EndpointConfig) -> dict:
            return {
                "type": ep.type,
                "label": ep.label,
                "options": {k: ("•••" if k.lower() in _SENSITIVE_KEYS else v)
                            for k, v in ep.options.items()},
            }
        return {
            "source": safe(self.source),
            "target": safe(self.target),
            "rules": dataclasses.asdict(self.rules),
            "sample_limit": self.sample_limit,
            "batch_size": self.batch_size,
            "mask_values": self.mask_values,
            "workers": self.workers,
            "strategy": self.strategy,
            "retry_attempts": self.retry_attempts,
            "checkpoint": bool(self.checkpoint),
            "incremental": dict(self.incremental),
        }


def _endpoint(data: dict, key: str) -> EndpointConfig:
    if not isinstance(data, dict) or "type" not in data:
        raise ValueError(f"Section '{key}' must contain a type field "
                         f"(sqlite | postgres | oracle | mssql)")
    options = {k: v for k, v in data.items() if k not in ("type", "label")}
    return EndpointConfig(type=str(data["type"]), label=data.get("label"),
                          options=options)


def _rules(data: dict) -> NormalizeRules:
    allowed = {f.name for f in dataclasses.fields(NormalizeRules)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(
            f"Unknown normalization rules: {sorted(unknown)}. "
            f"Allowed: {sorted(allowed)}"
        )
    return NormalizeRules(**data)


def _strategy(value) -> str:
    v = str(value).lower()
    if v not in ("auto", "stream", "hash"):
        raise ValueError(f"strategy: expected auto|stream|hash, got {value!r}")
    return v


# ---------------------------------------------------------------------------
# Validation of the "raw" config dict (before building Config, without
# connecting to the databases). Used by the `dbparity validate` command and
# by config_from_dict itself.
# ---------------------------------------------------------------------------

_ENDPOINT_TYPES = ("sqlite", "postgres", "postgresql", "oracle", "mssql")

# All known top-level keys (per Config fields)
_TOP_LEVEL_KEYS = {
    "source", "target", "tables", "pk_overrides", "exclude_columns", "rules",
    "sample_limit", "batch_size", "mask_values", "workers", "strategy",
    "hash_leaf_rows", "checkpoint", "checkpoint_every_rows",
    "retry_attempts", "retry_backoff_s", "report", "incremental",
}

# Minimums for integer parameters (kept in sync with config_from_dict)
_INT_MINIMUMS = {
    "workers": 1,
    "sample_limit": 0,
    "batch_size": 1,
    "hash_leaf_rows": 1,
    "checkpoint_every_rows": 1000,
    "retry_attempts": 1,
}


def _is_int(value) -> bool:
    """An integer (bool is an int in Python too, so exclude it explicitly)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    """A number (int or float), but not bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_endpoint(section: str, data, problems: list[str]) -> None:
    """Check the source/target section: type and required options per type."""
    if not isinstance(data, dict):
        problems.append(f"{section}: the section must be a mapping "
                        f"with a type field and connection options")
        return
    raw_type = data.get("type")
    if raw_type is None:
        problems.append(f"{section}.type: required field "
                        f"({' | '.join(_ENDPOINT_TYPES)})")
        return
    etype = str(raw_type).lower()
    if etype not in _ENDPOINT_TYPES:
        problems.append(f"{section}.type: unknown type {raw_type!r} "
                        f"(allowed: {', '.join(_ENDPOINT_TYPES)})")
        return
    if etype == "sqlite":
        if not data.get("path"):
            problems.append(f"{section}.path: required for type=sqlite")
    elif etype in ("postgres", "postgresql"):
        if not data.get("dsn"):
            missing = [k for k in ("host", "dbname", "user")
                       if not data.get(k)
                       and not (k == "dbname" and data.get("database"))]
            if missing:
                problems.append(
                    f"{section}: for type={etype} provide dsn or "
                    f"host+dbname+user (missing: {', '.join(missing)})")
    elif etype == "oracle":
        for key in ("user", "password", "dsn"):
            if not data.get(key):
                problems.append(f"{section}.{key}: required for type=oracle")
    elif etype == "mssql":
        if not data.get("dsn"):
            problems.append(f"{section}.dsn: required for type=mssql")


def _validate_rules_dict(data, problems: list[str]) -> None:
    """Check the rules section: known keys and value types."""
    if data is None:
        return
    if not isinstance(data, dict):
        problems.append("rules: expected a mapping of normalization rules")
        return
    defaults = NormalizeRules()
    allowed = sorted(f.name for f in dataclasses.fields(NormalizeRules))
    for key, value in data.items():
        if key not in allowed:
            hint = difflib.get_close_matches(str(key), allowed, n=1)
            msg = f"rules.{key}: unknown rule (typo?)"
            if hint:
                msg += f" — did you mean '{hint[0]}'"
            else:
                msg += f"; allowed: {', '.join(allowed)}"
            problems.append(msg)
            continue
        expected = getattr(defaults, key)
        if isinstance(expected, bool):
            if not isinstance(value, bool):
                problems.append(f"rules.{key}: expected true/false, "
                                f"got {value!r}")
        elif key == "timestamp_precision":
            if not _is_int(value) or not 0 <= value <= 6:
                problems.append(f"rules.{key}: expected an integer 0..6, "
                                f"got {value!r}")
        elif key == "float_epsilon":
            if not _is_number(value) or value < 0:
                problems.append(f"rules.{key}: expected a number ≥ 0, "
                                f"got {value!r}")


def validate_config_dict(data: dict) -> list[str]:
    """Checks the config dict BEFORE building Config and without connecting
    to the databases.

    Returns a list of human-readable problems (an empty list means the config
    is valid). Each line contains the field path, for example:
    "source.path: required for type=sqlite".
    """
    if not isinstance(data, dict):
        return ["Config is empty or malformed (a YAML mapping is expected)"]

    problems: list[str] = []

    # --- required source/target sections -----------------------------------
    for section in ("source", "target"):
        if section not in data:
            problems.append(f"{section}: missing required section "
                            f"(connection description)")
        else:
            _validate_endpoint(section, data[section], problems)

    # --- normalization rules ------------------------------------------------
    _validate_rules_dict(data.get("rules"), problems)

    # --- strategy -------------------------------------------------------------
    if "strategy" in data:
        v = data["strategy"]
        if not isinstance(v, str) or v.lower() not in ("auto", "stream", "hash"):
            problems.append(f"strategy: expected auto|stream|hash, "
                            f"got {v!r}")

    # --- integer parameters and their minimums --------------------------------
    for key, minimum in _INT_MINIMUMS.items():
        if key in data:
            v = data[key]
            if not _is_int(v):
                problems.append(f"{key}: expected an integer ≥ {minimum}, "
                                f"got {v!r}")
            elif v < minimum:
                problems.append(f"{key}: the minimum allowed value is "
                                f"{minimum}, got {v}")

    if "retry_backoff_s" in data:
        v = data["retry_backoff_s"]
        if not _is_number(v) or v < 0:
            problems.append(f"retry_backoff_s: expected a number ≥ 0, "
                            f"got {v!r}")

    if "mask_values" in data and data["mask_values"] is not None \
            and not isinstance(data["mask_values"], bool):
        problems.append(f"mask_values: expected true/false, "
                        f"got {data['mask_values']!r}")

    # --- table list ------------------------------------------------------------
    if data.get("tables") is not None:
        tables = data["tables"]
        if not isinstance(tables, list):
            problems.append("tables: expected a list of table names (strings)")
        else:
            for i, item in enumerate(tables):
                if not isinstance(item, str):
                    problems.append(f"tables[{i}]: expected a string, "
                                    f"got {item!r}")

    # --- PK overrides and excluded columns --------------------------------------
    for key in ("pk_overrides", "exclude_columns"):
        if data.get(key) is None:
            continue
        mapping = data[key]
        if not isinstance(mapping, dict):
            problems.append(f"{key}: expected a mapping "
                            f"{{table: [list of columns]}}")
            continue
        for table, cols in mapping.items():
            if not isinstance(cols, list) \
                    or not all(isinstance(c, str) for c in cols):
                problems.append(f"{key}.{table}: expected a list "
                                f"of column names (strings)")

    # --- incremental mode (watermark columns) ------------------------------------
    if data.get("incremental") is not None:
        mapping = data["incremental"]
        if not isinstance(mapping, dict):
            problems.append("incremental: expected a mapping "
                            "{table: watermark column}")
        else:
            for table, col in mapping.items():
                if not isinstance(col, str) or not col.strip():
                    problems.append(f"incremental.{table}: expected a "
                                    f"watermark column name (string)")

    # --- reports and checkpoint --------------------------------------------------
    report = data.get("report")
    if report is not None:
        if not isinstance(report, dict):
            problems.append("report: expected a mapping "
                            "with html and/or json keys")
        else:
            for key in ("html", "json"):
                if report.get(key) is not None \
                        and not isinstance(report[key], str):
                    problems.append(f"report.{key}: expected a string "
                                    f"(file path)")

    if data.get("checkpoint") is not None \
            and not isinstance(data["checkpoint"], str):
        problems.append("checkpoint: expected a string (file path)")

    # --- unknown top-level keys (with a hint) -------------------------------------
    for key in data:
        if key not in _TOP_LEVEL_KEYS:
            hint = difflib.get_close_matches(str(key),
                                             sorted(_TOP_LEVEL_KEYS), n=1)
            msg = f"{key}: unknown key (typo?)"
            if hint:
                msg += f" — did you mean '{hint[0]}'"
            problems.append(msg)

    return problems


def config_from_dict(data: dict) -> Config:
    problems = validate_config_dict(data)
    if problems:
        raise ValueError(
            f"Config failed validation ({len(problems)} problem(s)):\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
    report = data.get("report") or {}
    return Config(
        source=_endpoint(data["source"], "source"),
        target=_endpoint(data["target"], "target"),
        tables=data.get("tables"),
        pk_overrides={str(k).lower(): [str(c).lower() for c in v]
                      for k, v in (data.get("pk_overrides") or {}).items()},
        exclude_columns={str(k).lower(): [str(c).lower() for c in v]
                         for k, v in (data.get("exclude_columns") or {}).items()},
        rules=_rules(data.get("rules") or {}),
        sample_limit=int(data.get("sample_limit", 50)),
        batch_size=int(data.get("batch_size", 5000)),
        mask_values=bool(data.get("mask_values", False)),
        workers=max(1, int(data.get("workers", 1))),
        strategy=_strategy(data.get("strategy", "auto")),
        hash_leaf_rows=max(1, int(data.get("hash_leaf_rows", 20000))),
        checkpoint=(str(data["checkpoint"]) if data.get("checkpoint") else None),
        checkpoint_every_rows=max(1000, int(data.get("checkpoint_every_rows",
                                                     500000))),
        incremental={str(k).lower(): str(v).lower()
                     for k, v in (data.get("incremental") or {}).items()},
        retry_attempts=max(1, int(data.get("retry_attempts", 1))),
        retry_backoff_s=max(0.0, float(data.get("retry_backoff_s", 2.0))),
        report=ReportConfig(html=report.get("html"), json=report.get("json")),
    )


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with open(p, encoding="utf-8") as f:
        return config_from_dict(yaml.safe_load(f))
