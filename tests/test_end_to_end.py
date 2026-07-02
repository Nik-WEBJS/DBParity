"""Интеграционные тесты: демо-прогон end-to-end против EXPECTED."""
import json
import sqlite3

from dbparity import cli
from dbparity.config import Config, EndpointConfig
from dbparity.core import engine
from dbparity.demo.seed import EXPECTED, build_demo
from dbparity.report.render import render_html, write_json


def test_demo_run_matches_expected(tmp_path):
    cfg = build_demo(tmp_path)
    run = engine.run(cfg)
    by = {t.table: t for t in run.tables}

    for key, exp in EXPECTED["customers"].items():
        assert getattr(by["customers"], key) == exp, f"customers.{key}"
    for key, exp in EXPECTED["orders"].items():
        assert getattr(by["orders"], key) == exp, f"orders.{key}"
    assert by["products"].total_diffs == 0
    assert by["products"].src_rows == EXPECTED["products"]["src_rows"]

    assert run.tables_only_in_source == EXPECTED["only_in_source"]
    assert run.tables_only_in_target == EXPECTED["only_in_target"]

    sd = {d.table: d for d in run.schema_diffs}
    assert list(sd) == ["orders"]
    assert sd["orders"].missing_in_target == \
        EXPECTED["schema_diffs"]["orders"]["missing_in_target"]

    assert not run.equivalent
    assert run.totals["total_diffs"] == 12   # customers 9 + orders 3


def test_html_report(tmp_path):
    run = engine.run(build_demo(tmp_path))
    html = render_html(run)
    assert "НЕ ЭКВИВАЛЕНТНО" in html
    assert "tabler" in html            # UI-кит подключён
    assert "chart.umd" in html         # графики подключены
    assert "customers" in html


def test_json_report(tmp_path):
    run = engine.run(build_demo(tmp_path))
    p = write_json(run, tmp_path / "r.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["equivalent"] is False
    assert data["totals"]["total_diffs"] == 12
    kinds = {s["kind"] for t in data["tables"] for s in t["samples"]}
    assert "mismatch" in kinds and "missing_in_target" in kinds


def test_traps_do_not_false_positive(tmp_path):
    """Ловушки (таймзона, паддинг, ''/NULL) не должны давать ложных расхождений."""
    run = engine.run(build_demo(tmp_path))
    by = {t.table: t for t in run.tables}
    flagged = set()
    for s in by["customers"].samples:
        flagged.add(s.pk[0])
    # id 60 (таймзона) и id 70 (паддинг) не должны попасть в расхождения
    assert "60" not in flagged and "70" not in flagged
    # а настоящие расхождения — должны
    assert {"10", "20", "30", "40"} <= flagged


def test_parallel_workers_match_sequential(tmp_path):
    """workers=3 даёт побитово те же результаты, что последовательный прогон."""
    import dataclasses
    cfg = build_demo(tmp_path)
    seq = engine.run(cfg)
    par = engine.run(dataclasses.replace(cfg, workers=3))
    assert [t.table for t in par.tables] == [t.table for t in seq.tables]
    assert {t.table: (t.total_diffs, t.matched, t.src_rows, t.dst_rows)
            for t in par.tables} == \
           {t.table: (t.total_diffs, t.matched, t.src_rows, t.dst_rows)
            for t in seq.tables}
    assert par.totals == seq.totals


def test_progress_callback(tmp_path):
    """Колбэк прогресса получает финальные счётчики строк по таблицам."""
    cfg = build_demo(tmp_path)
    seen = {}
    engine.run(cfg, on_progress=lambda t, n: seen.__setitem__(t, n))
    assert seen["orders"] == 5000 + 5000        # src + dst
    assert seen["customers"] == 1200 + 1199


def test_text_pk_warning(tmp_path):
    """Текстовый PK → предупреждение о коллациях, но не ошибка."""
    for name in ("s.db", "d.db"):
        c = sqlite3.connect(tmp_path / name)
        c.execute("CREATE TABLE t (code TEXT PRIMARY KEY, v TEXT)")
        c.executemany("INSERT INTO t VALUES (?, ?)", [("a", "1"), ("b", "2")])
        c.commit()
        c.close()
    cfg = Config(
        source=EndpointConfig("sqlite", None, {"path": str(tmp_path / "s.db")}),
        target=EndpointConfig("sqlite", None, {"path": str(tmp_path / "d.db")}),
    )
    run = engine.run(cfg)
    t = run.tables[0]
    assert t.warnings and "коллаци" in t.warnings[0]
    assert t.ok                      # предупреждение ≠ расхождение
    assert run.equivalent
    html = render_html(run)
    assert "коллаци" in html         # предупреждение видно в отчёте


def test_cli_demo_exit_code(tmp_path):
    rc = cli.main(["demo", "--outdir", str(tmp_path / "out")])
    assert rc == 1   # расхождения найдены — ненулевой код выхода
    assert (tmp_path / "out" / "dbparity_report.html").exists()
    assert (tmp_path / "out" / "dbparity_report.json").exists()
    assert (tmp_path / "out" / "demo_config.yaml").exists()
