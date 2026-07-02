"""Тесты валидации конфига (validate_config_dict) и команды `dbparity validate`."""
import pytest

from dbparity import cli
from dbparity.config import config_from_dict, validate_config_dict


def _valid_config() -> dict:
    """Минимальный валидный конфиг sqlite → sqlite со всеми основными секциями."""
    return {
        "source": {"type": "sqlite", "path": "src.db", "label": "SRC"},
        "target": {"type": "sqlite", "path": "dst.db"},
        "rules": {"rtrim_strings": True, "timestamp_precision": 3},
        "strategy": "auto",
        "workers": 2,
        "tables": ["customers", "orders"],
        "pk_overrides": {"orders": ["id"]},
        "exclude_columns": {"customers": ["updated_at"]},
        "report": {"html": "r.html", "json": "r.json"},
    }


def test_valid_config_is_ok():
    """Валидный конфиг — пустой список проблем, Config строится без ошибок."""
    assert validate_config_dict(_valid_config()) == []
    cfg = config_from_dict(_valid_config())
    assert cfg.workers == 2 and cfg.strategy == "auto"


def test_missing_source():
    """Отсутствие секции source — понятная проблема с именем секции."""
    data = _valid_config()
    del data["source"]
    problems = validate_config_dict(data)
    assert any(p.startswith("source:") and "обязательная секция" in p
               for p in problems)


def test_unknown_endpoint_type():
    """Неверный type эндпоинта — проблема со списком допустимых типов."""
    data = _valid_config()
    data["source"]["type"] = "sqllite"
    problems = validate_config_dict(data)
    assert any(p.startswith("source.type:") and "sqllite" in p
               and "sqlite" in p for p in problems)


def test_sqlite_requires_path():
    """sqlite без path — проблема с путём к полю и указанием типа."""
    data = _valid_config()
    del data["source"]["path"]
    problems = validate_config_dict(data)
    assert any(p.startswith("source.path:") and "type=sqlite" in p
               for p in problems)


def test_postgres_requires_connection_params():
    """postgres без dsn и host+dbname+user — перечисляются недостающие поля."""
    data = _valid_config()
    data["target"] = {"type": "postgres", "host": "db.local"}
    problems = validate_config_dict(data)
    assert any(p.startswith("target:") and "dsn" in p
               and "dbname" in p and "user" in p for p in problems)
    # с dsn — претензий к подключению нет
    data["target"] = {"type": "postgres", "dsn": "postgresql://u@h/db"}
    assert validate_config_dict(data) == []


def test_rules_typo_gets_hint():
    """Опечатка в ключе rules — подсказка ближайшего известного правила."""
    data = _valid_config()
    data["rules"] = {"rtrim_string": True}
    problems = validate_config_dict(data)
    assert any(p.startswith("rules.rtrim_string:")
               and "rtrim_strings" in p for p in problems)


def test_top_level_typo_gets_hint():
    """Опечатка верхнего уровня — предупреждение с подсказкой ближайшего ключа."""
    data = _valid_config()
    data["wokers"] = 4
    problems = validate_config_dict(data)
    assert any("wokers" in p and "неизвестный ключ" in p
               and "workers" in p for p in problems)


def test_workers_wrong_type_and_minimum():
    """workers: не целое или меньше минимума — обе ситуации ловятся."""
    data = _valid_config()
    data["workers"] = "два"
    problems = validate_config_dict(data)
    assert any(p.startswith("workers:") and "целое" in p for p in problems)

    data["workers"] = 0
    problems = validate_config_dict(data)
    assert any(p.startswith("workers:") and "1" in p for p in problems)


def test_config_from_dict_collects_all_problems():
    """config_from_dict сообщает сразу ВСЕ проблемы одной ошибкой."""
    data = _valid_config()
    del data["source"]["path"]          # проблема 1
    data["strategy"] = "fast"           # проблема 2
    data["workers"] = "много"           # проблема 3
    with pytest.raises(ValueError) as exc:
        config_from_dict(data)
    text = str(exc.value)
    assert "source.path" in text
    assert "strategy" in text
    assert "workers" in text
    assert "проблем: 3" in text


def test_cli_validate_exit_codes(tmp_path):
    """Коды выхода validate: 0 — валиден, 1 — проблемы, 2 — нет файла/кривой YAML."""
    from dbparity.demo.seed import build_demo

    # 0: демо-конфиг обязан проходить новую валидацию
    build_demo(tmp_path / "demo")
    assert cli.main(["validate", "-c",
                     str(tmp_path / "demo" / "demo_config.yaml")]) == 0

    # 1: конфиг с проблемами (нет target.path, опечатка в rules)
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "source: {type: sqlite, path: s.db}\n"
        "target: {type: sqlite}\n"
        "rules: {rtrim_string: true}\n",
        encoding="utf-8",
    )
    assert cli.main(["validate", "-c", str(bad)]) == 1

    # 2: файл не существует
    assert cli.main(["validate", "-c", str(tmp_path / "нет_такого.yaml")]) == 2

    # 2: не разбирается как YAML
    broken = tmp_path / "broken.yaml"
    broken.write_text("source: {type: sqlite\n  path: [::", encoding="utf-8")
    assert cli.main(["validate", "-c", str(broken)]) == 2
