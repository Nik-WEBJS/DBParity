"""Золотой тест формата config.yaml (v0.9: semver-гарантии).

Правила эволюции — как у JSON-отчёта (docs/report-format.md):
- ДОБАВЛЕНИЕ нового ключа — минорное изменение: дополни ЗАМОРОЖЕННЫЕ
  наборы ниже и docs/config-reference.md;
- ПЕРЕИМЕНОВАНИЕ/УДАЛЕНИЕ ключа или смена типа/семантики — мажорное:
  разрешено только с мажорной версией и записью в CHANGELOG.
Падение этого теста без осознанного изменения наборов = случайная
поломка обратной совместимости пользовательских конфигов.
"""
import dataclasses

from dbparity.config import _TOP_LEVEL_KEYS, config_from_dict
from dbparity.core.normalize import NormalizeRules

FROZEN_TOP_LEVEL = {
    "source", "target", "tables", "pk_overrides", "exclude_columns", "rules",
    "sample_limit", "batch_size", "mask_values", "workers", "strategy",
    "hash_leaf_rows", "checkpoint", "checkpoint_every_rows",
    "retry_attempts", "retry_backoff_s", "report", "incremental",
}

FROZEN_RULES = {
    "oracle_empty_string_is_null", "rtrim_strings", "unicode_nfc",
    "float_epsilon", "yn_as_bool", "truncate_time_if_midnight",
    "timestamp_precision", "tz_to_utc", "bytes_as_md5",
}


def test_top_level_keys_frozen():
    assert _TOP_LEVEL_KEYS == FROZEN_TOP_LEVEL, (
        "Набор ключей config.yaml изменился: если это осознанное ДОБАВЛЕНИЕ — "
        "дополните FROZEN_TOP_LEVEL и docs/config-reference.md; удаление/"
        "переименование допустимо только мажорной версией")


def test_rules_keys_frozen():
    actual = {f.name for f in dataclasses.fields(NormalizeRules)}
    assert actual == FROZEN_RULES, (
        "Набор правил нормализации изменился: добавление — дополните "
        "FROZEN_RULES и docs/config-reference.md; удаление/переименование — "
        "только мажорной версией")


def test_full_config_still_parses():
    """Конфиг, использующий каждый замороженный ключ, собирается без ошибок."""
    cfg = config_from_dict({
        "source": {"type": "sqlite", "label": "S", "path": "/tmp/s.db"},
        "target": {"type": "postgres", "label": "T",
                   "dsn": "host=x dbname=y user=z"},
        "tables": ["a", "b"],
        "pk_overrides": {"a": ["id", "ts"]},
        "exclude_columns": {"b": ["etl_ts"]},
        "rules": {k: v for k, v in (
            ("oracle_empty_string_is_null", True), ("rtrim_strings", True),
            ("unicode_nfc", True), ("float_epsilon", 1e-9),
            ("yn_as_bool", False), ("truncate_time_if_midnight", True),
            ("timestamp_precision", 3), ("tz_to_utc", True),
            ("bytes_as_md5", True))},
        "sample_limit": 10,
        "batch_size": 1000,
        "mask_values": True,
        "workers": 2,
        "strategy": "auto",
        "hash_leaf_rows": 5000,
        "checkpoint": "state.json",
        "checkpoint_every_rows": 100000,
        "retry_attempts": 3,
        "retry_backoff_s": 1.5,
        "incremental": {"a": "updated_at"},
        "report": {"html": "r.html", "json": "r.json"},
    })
    assert cfg.workers == 2 and cfg.incremental == {"a": "updated_at"}
    assert cfg.rules.timestamp_precision == 3
