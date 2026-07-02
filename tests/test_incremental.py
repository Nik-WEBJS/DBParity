"""Инкрементальный режим (v0.5): watermark-фильтр, стейт, детект дрейфа."""
import dataclasses
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import yaml

from dbparity import cli
from dbparity.config import (Config, EndpointConfig, config_from_dict,
                             validate_config_dict)
from dbparity.core import engine
from dbparity.core.incremental import (IncrementalState, default_state_path,
                                       state_fingerprint)

# updated_at — числовой watermark (детерминированнее timestamp'ов);
# значения различны, чтобы фильтр >= wm захватывал минимум строк
ROWS = [(1, "a", 101), (2, "b", 102), (3, "c", 103), (4, "d", 104), (5, "e", 105)]


def _mkdb(path, rows=ROWS) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, "
                 "v TEXT, updated_at INTEGER)")
    conn.executemany("INSERT INTO items VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _touch(path, row_id: int, v: str, wm: int) -> None:
    """Обновляет строку: новое значение + рост watermark-колонки."""
    conn = sqlite3.connect(path)
    conn.execute("UPDATE items SET v = ?, updated_at = ? WHERE id = ?",
                 (v, wm, row_id))
    conn.commit()
    conn.close()


def _cfg(tmp_path, **overrides) -> Config:
    params = dict(
        source=EndpointConfig("sqlite", None, {"path": str(tmp_path / "s.db")}),
        target=EndpointConfig("sqlite", None, {"path": str(tmp_path / "d.db")}),
        incremental={"items": "updated_at"},
    )
    params.update(overrides)
    return Config(**params)


def _setup(tmp_path, monkeypatch) -> Config:
    """Пара идентичных баз + chdir: авто-стейт .dbparity_incr_* пишется в tmp."""
    monkeypatch.chdir(tmp_path)
    _mkdb(tmp_path / "s.db")
    _mkdb(tmp_path / "d.db")
    return _cfg(tmp_path)


def _items(run):
    return {t.table: t for t in run.tables}["items"]


def _saved_wm(cfg):
    """Watermark из стейт-файла на диске (свежая загрузка)."""
    fp = state_fingerprint(cfg)
    return IncrementalState.load_or_create(
        default_state_path(fp), fp).last_watermark("items")


# ---- интеграция (sqlite) ---------------------------------------------------

def test_first_run_full_and_state_created(tmp_path, monkeypatch):
    """Прогон 1 (стейта нет): сверено всё, стейт создан с max(updated_at)."""
    cfg = _setup(tmp_path, monkeypatch)
    tr = _items(engine.run(cfg))
    assert tr.error is None
    assert tr.src_rows == 5 and tr.dst_rows == 5 and tr.matched == 5
    assert Path(default_state_path(state_fingerprint(cfg))).exists()
    assert _saved_wm(cfg) == 105
    assert cfg.summary()["incremental"] == {"items": "updated_at"}


def test_second_run_checks_only_changed(tmp_path, monkeypatch):
    """Синхронное обновление в обеих БД: прогон 2 сверяет только его."""
    cfg = _setup(tmp_path, monkeypatch)
    engine.run(cfg)                                 # полный прогон + стейт
    for db in ("s.db", "d.db"):
        _touch(tmp_path / db, 5, "e2", 106)
    tr = _items(engine.run(cfg))
    assert tr.error is None
    assert tr.src_rows == 1 and tr.dst_rows == 1    # только изменённая строка
    assert tr.matched == 1 and tr.total_diffs == 0
    assert any("Инкрементальная сверка" in w for w in tr.warnings)
    assert _saved_wm(cfg) == 106                    # watermark продвинулся


def test_drift_detected_as_missing(tmp_path, monkeypatch):
    """Строка обновлена только в источнике → missing_in_target (дрейф)."""
    cfg = _setup(tmp_path, monkeypatch)
    engine.run(cfg)
    _touch(tmp_path / "s.db", 2, "B!", 110)         # dual-write потерял запись
    tr = _items(engine.run(cfg))
    assert tr.error is None
    assert tr.missing_in_target == 1 and tr.total_diffs == 1
    assert any("дрейф dual-write" in w for w in tr.warnings)
    assert _saved_wm(cfg) == 110


def test_full_flag_rechecks_everything(tmp_path, monkeypatch):
    """full=True: фильтр игнорируется, сверяется всё, стейт обновляется."""
    cfg = _setup(tmp_path, monkeypatch)
    engine.run(cfg)
    for db in ("s.db", "d.db"):
        _touch(tmp_path / db, 1, "a2", 107)
    tr = _items(engine.run(cfg, full=True))
    assert tr.src_rows == 5 and tr.dst_rows == 5    # сверено всё
    assert tr.matched == 5 and tr.total_diffs == 0
    assert any("watermark проигнорирован" in w for w in tr.warnings)
    assert _saved_wm(cfg) == 107                    # стейт всё равно обновлён


def test_fingerprint_change_ignores_state(tmp_path, monkeypatch):
    """Смена конфига меняет fingerprint → старый стейт не используется."""
    cfg = _setup(tmp_path, monkeypatch)
    engine.run(cfg)
    assert _saved_wm(cfg) == 105
    cfg2 = dataclasses.replace(cfg, strategy="stream")
    assert state_fingerprint(cfg2) != state_fingerprint(cfg)
    # смена watermark-колонки тоже инвалидирует стейт
    assert state_fingerprint(
        dataclasses.replace(cfg, incremental={"items": "id"})
    ) != state_fingerprint(cfg)
    tr = _items(engine.run(cfg2))
    assert tr.src_rows == 5                         # полная сверка заново
    # юнит: тот же путь, чужой fingerprint → watermark не виден
    st = IncrementalState.load_or_create(
        default_state_path(state_fingerprint(cfg)), "другой-отпечаток")
    assert st.last_watermark("items") is None


def test_missing_watermark_column_is_error(tmp_path, monkeypatch):
    """Несуществующая watermark-колонка → error-tr с внятным сообщением."""
    monkeypatch.chdir(tmp_path)
    _mkdb(tmp_path / "s.db")
    _mkdb(tmp_path / "d.db")
    cfg = _cfg(tmp_path, incremental={"items": "no_such_col"})
    tr = _items(engine.run(cfg))
    assert tr.status == "error"
    assert "no_such_col" in tr.error and "watermark" in tr.error.lower()


def test_incremental_beats_hash(tmp_path, monkeypatch):
    """Таблица digest-eligible, но в incremental → stream-путь + заметка."""
    cfg = dataclasses.replace(_setup(tmp_path, monkeypatch), strategy="hash")
    # контроль: без incremental эта таблица действительно идёт hash-путём
    assert _items(engine.run(
        dataclasses.replace(cfg, incremental={}))).mode == "hash"
    tr = _items(engine.run(cfg))
    assert tr.error is None and tr.mode == "stream"
    assert any("hash-сверка отключена" in w for w in tr.warnings)
    assert _saved_wm(cfg) == 105                    # трекинг работал


# ---- юниты стейта ------------------------------------------------------------

def test_state_unit_roundtrip_and_atomicity(tmp_path):
    """Стейт-файл: запись/повторная загрузка, атомарность, битые данные."""
    p = tmp_path / "incr.json"
    st = IncrementalState.load_or_create(p, "fp")
    assert st.last_watermark("t") is None           # пустой стейт
    st.update("t", Decimal(42))                     # интегральный Decimal — ок
    assert st.last_watermark("t") == 42
    assert not p.with_suffix(".json.tmp").exists()  # tmp-файл подменён
    assert json.loads(p.read_text(encoding="utf-8"))["fingerprint"] == "fp"

    st2 = IncrementalState.load_or_create(p, "fp")  # повторная загрузка
    assert st2.last_watermark("t") == 42
    st2.update("t", 5.5)                            # float неэнкодируем...
    assert st2.last_watermark("t") == 42            # ...старый wm сохранён

    p.write_text("{оборвано", encoding="utf-8")     # битый файл
    st3 = IncrementalState.load_or_create(p, "fp")
    assert st3.last_watermark("t") is None          # чистый стейт, без исключений


# ---- конфиг и CLI ------------------------------------------------------------

def test_config_parsing_validation_and_cli_full(tmp_path, monkeypatch):
    """Парсинг/валидация карты incremental и прокидка --full через CLI."""
    monkeypatch.chdir(tmp_path)
    _mkdb(tmp_path / "s.db")
    _mkdb(tmp_path / "d.db")
    conf = {
        "source": {"type": "sqlite", "path": str(tmp_path / "s.db")},
        "target": {"type": "sqlite", "path": str(tmp_path / "d.db")},
        "incremental": {"Items": "Updated_At"},     # нормализация регистра
    }
    assert validate_config_dict(conf) == []
    assert config_from_dict(conf).incremental == {"items": "updated_at"}
    # невалидные варианты ловятся с путём к полю
    assert any(p.startswith("incremental.items")
               for p in validate_config_dict(dict(conf,
                                                  incremental={"items": 5})))
    assert any(p.startswith("incremental:")
               for p in validate_config_dict(dict(conf,
                                                  incremental=["items"])))

    (tmp_path / "c.yaml").write_text(yaml.safe_dump(conf), encoding="utf-8")
    assert cli.main(["compare", "-c", str(tmp_path / "c.yaml")]) == 0
    # дрейф только в источнике: --full сверяет всё и находит его
    _touch(tmp_path / "s.db", 2, "B!", 110)
    assert cli.main(["compare", "-c", str(tmp_path / "c.yaml"), "--full"]) == 1
