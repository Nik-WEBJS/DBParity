"""«Золотой» тест формата JSON-отчёта (schema v1, docs/report-format.md).

Ловит случайное изменение замороженного формата в CI:

- УДАЛЕНИЕ или ПЕРЕИМЕНОВАНИЕ ключа из замороженного набора — тест падает:
  это мажорное изменение, требующее инкремента REPORT_SCHEMA_VERSION
  и правки docs/report-format.md.
- ДОБАВЛЕНИЕ новых ключей тест сознательно ДОПУСКАЕТ (проверяется
  «замороженный набор ⊆ фактического», а не строгое равенство) — это
  минорное изменение. ВНИМАНИЕ: каждый новый ключ обязан быть описан
  в docs/report-format.md — тест этого проверить не может.
- Строгое равенство набора ключей — только для элементов samples[]
  (контракт v1 фиксирует ровно {kind, pk, columns}).
"""
import json

import pytest

from dbparity.core import engine
from dbparity.core.models import REPORT_SCHEMA_VERSION
from dbparity.demo.seed import build_demo
from dbparity.report.render import render_html, write_json

# --- замороженные наборы ключей схемы v1 (см. docs/report-format.md) --------

FROZEN_TOP_LEVEL = {
    "schema_version",
    "source_label",
    "target_label",
    "started_at",
    "finished_at",
    "tables",
    "schema_diffs",
    "tables_only_in_source",
    "tables_only_in_target",
    "config_summary",
    "equivalent",
    "totals",
}

FROZEN_TABLE_KEYS = {
    "table",
    "pk",
    "src_rows",
    "dst_rows",
    "matched",
    "mismatched",
    "missing_in_target",
    "extra_in_target",
    "duplicate_pk",
    "null_pk",
    "samples",
    "column_mismatch_counts",
    "warnings",
    "error",
    "duration_s",
    "mode",
    "rows_hash_matched",
    "rows_streamed",
    "segments_matched",
    "segments_streamed",
    "total_diffs",
    "status",
    "match_pct",
}

# Единственный строгий контракт: у сэмпла РОВНО эти три ключа
SAMPLE_KEYS = {"kind", "pk", "columns"}

KNOWN_KINDS = {
    "missing_in_target",
    "extra_in_target",
    "mismatch",
    "duplicate_pk",
    "null_pk",
}

FROZEN_TOTALS_KEYS = {
    "tables_total",
    "tables_ok",
    "src_rows",
    "dst_rows",
    "matched",
    "mismatched",
    "missing_in_target",
    "extra_in_target",
    "duplicate_pk",
    "null_pk",
    "total_diffs",
    "match_pct",
}

FROZEN_SCHEMA_DIFF_KEYS = {
    "table",
    "missing_in_target",
    "extra_in_target",
    "type_changes",
    "pk_mismatch",
}


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """Один демо-прогон на модуль: RunResult + JSON, перечитанный с диска.

    Проверяем именно записанный write_json артефакт — это и есть
    замороженный формат обмена, а не промежуточный python-словарь.
    """
    tmp = tmp_path_factory.mktemp("report_schema")
    run = engine.run(build_demo(tmp))
    path = write_json(run, tmp / "report.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return run, data


def _missing(frozen: set, actual: dict, where: str) -> str:
    lost = frozen - set(actual)
    return (f"{where}: из замороженной схемы v1 исчезли ключи {sorted(lost)} — "
            f"это мажорное изменение: инкрементируйте REPORT_SCHEMA_VERSION "
            f"и обновите docs/report-format.md")


def test_schema_version_frozen(report):
    _, data = report
    assert data["schema_version"] == REPORT_SCHEMA_VERSION
    assert data["schema_version"] == 1, (
        "Версия схемы изменилась — обновите замороженные наборы этого теста "
        "и docs/report-format.md")
    # schema_version — первое смысловое поле отчёта
    assert next(iter(data)) == "schema_version"


def test_top_level_keys_frozen(report):
    _, data = report
    # Не строгое равенство: НОВЫЕ ключи допустимы (минорное изменение),
    # но обязаны быть описаны в docs/report-format.md.
    assert FROZEN_TOP_LEVEL <= set(data), _missing(
        FROZEN_TOP_LEVEL, data, "верхний уровень")


def test_table_keys_frozen(report):
    _, data = report
    assert data["tables"], "демо-прогон обязан вернуть таблицы"
    # tables[0] — представитель; заодно проверяем каждый элемент:
    # набор ключей у всех записей таблиц одинаков.
    first_keys = set(data["tables"][0])
    assert FROZEN_TABLE_KEYS <= first_keys, _missing(
        FROZEN_TABLE_KEYS, data["tables"][0], "tables[0]")
    for t in data["tables"]:
        # новые ключи допустимы (см. докстринг модуля), пропажа — нет
        assert FROZEN_TABLE_KEYS <= set(t), _missing(
            FROZEN_TABLE_KEYS, t, f"tables[{t.get('table')!r}]")
        assert set(t) == first_keys, "набор ключей таблиц неоднороден"


def test_sample_keys_strict(report):
    _, data = report
    samples = [s for t in data["tables"] for s in t["samples"]]
    assert samples, "демо-прогон обязан дать примеры расхождений"
    for s in samples:
        # Здесь — СТРОГОЕ равенство: контракт v1 фиксирует ровно три ключа.
        # Добавление ключа в сэмпл = осознанное изменение формата:
        # обновите docs/report-format.md и этот тест.
        assert set(s) == SAMPLE_KEYS, f"ключи сэмпла изменились: {sorted(s)}"
        assert s["kind"] in KNOWN_KINDS, f"неизвестный kind: {s['kind']!r}"
        if s["kind"] == "mismatch":
            assert isinstance(s["columns"], dict) and s["columns"]
        else:
            assert s["columns"] is None
    # демо покрывает основные виды расхождений — проверка не «вакуумная»
    kinds = {s["kind"] for s in samples}
    assert {"mismatch", "missing_in_target", "extra_in_target"} <= kinds


def test_totals_and_schema_diff_keys_frozen(report):
    _, data = report
    assert FROZEN_TOTALS_KEYS <= set(data["totals"]), _missing(
        FROZEN_TOTALS_KEYS, data["totals"], "totals")
    assert data["schema_diffs"], "демо содержит различие схем (orders)"
    for sd in data["schema_diffs"]:
        assert FROZEN_SCHEMA_DIFF_KEYS <= set(sd), _missing(
            FROZEN_SCHEMA_DIFF_KEYS, sd, "schema_diffs[]")
    # ядро config_summary стабильно; состав может расширяться минорно
    assert {"source", "target", "rules"} <= set(data["config_summary"])


def test_html_footer_shows_schema_version(report):
    run, _ = report
    assert f"схема отчёта v{REPORT_SCHEMA_VERSION}" in render_html(run)
