"""Загрузка и валидация YAML-конфига сверки."""
from __future__ import annotations

import dataclasses
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
    hash_leaf_rows: int = 20000         # шаг бакета по PK (≈ строк в сегменте
                                        # при плотном ключе)
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
        }


def _endpoint(data: dict, key: str) -> EndpointConfig:
    if not isinstance(data, dict) or "type" not in data:
        raise ValueError(f"Секция '{key}' должна содержать поле type "
                         f"(sqlite | postgres | oracle | mssql)")
    options = {k: v for k, v in data.items() if k not in ("type", "label")}
    return EndpointConfig(type=str(data["type"]), label=data.get("label"),
                          options=options)


def _rules(data: dict) -> NormalizeRules:
    allowed = {f.name for f in dataclasses.fields(NormalizeRules)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(
            f"Неизвестные правила нормализации: {sorted(unknown)}. "
            f"Допустимые: {sorted(allowed)}"
        )
    return NormalizeRules(**data)


def _strategy(value) -> str:
    v = str(value).lower()
    if v not in ("auto", "stream", "hash"):
        raise ValueError(f"strategy: ожидается auto|stream|hash, получено {value!r}")
    return v


def config_from_dict(data: dict) -> Config:
    if not isinstance(data, dict):
        raise ValueError("Конфиг пуст или имеет неверный формат")
    for key in ("source", "target"):
        if key not in data:
            raise ValueError(f"В конфиге отсутствует обязательная секция '{key}'")
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
        report=ReportConfig(html=report.get("html"), json=report.get("json")),
    )


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Конфиг не найден: {p}")
    with open(p, encoding="utf-8") as f:
        return config_from_dict(yaml.safe_load(f))
