"""Tests for config validation (validate_config_dict) and `dbparity validate`."""
import pytest

from dbparity import cli
from dbparity.config import config_from_dict, validate_config_dict


def _valid_config() -> dict:
    """A minimal valid sqlite -> sqlite config with all the main sections."""
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
    """A valid config yields an empty problem list; Config builds cleanly."""
    assert validate_config_dict(_valid_config()) == []
    cfg = config_from_dict(_valid_config())
    assert cfg.workers == 2 and cfg.strategy == "auto"


def test_missing_source():
    """A missing source section - a clear problem naming the section."""
    data = _valid_config()
    del data["source"]
    problems = validate_config_dict(data)
    assert any(p.startswith("source:") and "missing required section" in p
               for p in problems)


def test_unknown_endpoint_type():
    """A wrong endpoint type - a problem listing the allowed types."""
    data = _valid_config()
    data["source"]["type"] = "sqllite"
    problems = validate_config_dict(data)
    assert any(p.startswith("source.type:") and "sqllite" in p
               and "sqlite" in p for p in problems)


def test_sqlite_requires_path():
    """sqlite without path - a problem with the field path and the type."""
    data = _valid_config()
    del data["source"]["path"]
    problems = validate_config_dict(data)
    assert any(p.startswith("source.path:") and "type=sqlite" in p
               for p in problems)


def test_postgres_requires_connection_params():
    """postgres without dsn and host+dbname+user - the missing fields are listed."""
    data = _valid_config()
    data["target"] = {"type": "postgres", "host": "db.local"}
    problems = validate_config_dict(data)
    assert any(p.startswith("target:") and "dsn" in p
               and "dbname" in p and "user" in p for p in problems)
    # with a dsn there are no connection complaints
    data["target"] = {"type": "postgres", "dsn": "postgresql://u@h/db"}
    assert validate_config_dict(data) == []


def test_rules_typo_gets_hint():
    """A typo in a rules key - a hint with the closest known rule."""
    data = _valid_config()
    data["rules"] = {"rtrim_string": True}
    problems = validate_config_dict(data)
    assert any(p.startswith("rules.rtrim_string:")
               and "rtrim_strings" in p for p in problems)


def test_top_level_typo_gets_hint():
    """A top-level typo - a warning with the closest-key hint."""
    data = _valid_config()
    data["wokers"] = 4
    problems = validate_config_dict(data)
    assert any("wokers" in p and "unknown key" in p
               and "workers" in p for p in problems)


def test_workers_wrong_type_and_minimum():
    """workers: non-integer or below the minimum - both cases are caught."""
    data = _valid_config()
    data["workers"] = "two"
    problems = validate_config_dict(data)
    assert any(p.startswith("workers:") and "integer" in p for p in problems)

    data["workers"] = 0
    problems = validate_config_dict(data)
    assert any(p.startswith("workers:") and "1" in p for p in problems)


def test_config_from_dict_collects_all_problems():
    """config_from_dict reports ALL the problems at once in a single error."""
    data = _valid_config()
    del data["source"]["path"]          # problem 1
    data["strategy"] = "fast"           # problem 2
    data["workers"] = "lots"            # problem 3
    with pytest.raises(ValueError) as exc:
        config_from_dict(data)
    text = str(exc.value)
    assert "source.path" in text
    assert "strategy" in text
    assert "workers" in text
    assert "3 problem" in text


def test_cli_validate_exit_codes(tmp_path):
    """validate exit codes: 0 - valid, 1 - problems, 2 - no file / broken YAML."""
    from dbparity.demo.seed import build_demo

    # 0: the demo config must pass the new validation
    build_demo(tmp_path / "demo")
    assert cli.main(["validate", "-c",
                     str(tmp_path / "demo" / "demo_config.yaml")]) == 0

    # 1: a config with problems (no target.path, a typo in rules)
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "source: {type: sqlite, path: s.db}\n"
        "target: {type: sqlite}\n"
        "rules: {rtrim_string: true}\n",
        encoding="utf-8",
    )
    assert cli.main(["validate", "-c", str(bad)]) == 1

    # 2: the file does not exist
    assert cli.main(["validate", "-c", str(tmp_path / "no_such_file.yaml")]) == 2

    # 2: does not parse as YAML
    broken = tmp_path / "broken.yaml"
    broken.write_text("source: {type: sqlite\n  path: [::", encoding="utf-8")
    assert cli.main(["validate", "-c", str(broken)]) == 2
