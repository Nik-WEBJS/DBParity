# Формат JSON-отчёта DBParity — схема v1

Справочник машиночитаемого отчёта, который DBParity пишет по пути
`report.json` из конфига (или через `dbparity.report.render.write_json`).
Отчёт — это сериализованный `RunResult.to_dict()`
(`dbparity/core/models.py`).

- Текущая версия схемы: **1** (константа `REPORT_SCHEMA_VERSION`
  в `dbparity/core/models.py`).
- Файл пишется в UTF-8, `ensure_ascii=False`, `indent=2`.
- Ключ `schema_version` всегда идёт первым — потребитель может проверить
  версию до разбора остального документа.
- Замороженный набор ключей охраняется тестом
  `tests/test_report_schema.py`.

## Гарантии совместимости

Формат подчиняется semver-подобным правилам (роадмап v0.9):

| Изменение | Класс | `schema_version` |
|---|---|---|
| Добавление нового ключа (на любом уровне) | минорное | не меняется |
| Удаление или переименование ключа | мажорное | инкремент |
| Смена типа или семантики значения существующего ключа | мажорное | инкремент |

Следствия для потребителей:

1. **Игнорируйте незнакомые ключи.** Новые ключи могут появиться в любом
   релизе без смены `schema_version`; каждый из них обязан быть описан
   в этом документе.
2. **Не полагайтесь на порядок ключей**, кроме гарантии «`schema_version`
   первый». Фактический порядок стабилен (порядок полей dataclass),
   но контрактом не является.
3. **Проверяйте `schema_version`** и отказывайтесь разбирать отчёт
   с бóльшей версией схемы, чем вы поддерживаете.
4. Ключи, описанные ниже как v1, не исчезнут и не сменят тип
   без инкремента `schema_version`.

## Верхний уровень

| Ключ | Тип | Семантика |
|---|---|---|
| `schema_version` | int | Версия схемы отчёта. В v1 всегда `1`. |
| `source_label` | string | Человекочитаемая метка источника: `label` эндпоинта из конфига либо автогенерированная (`sqlite:<path>` или тип эндпоинта). |
| `target_label` | string | То же для приёмника. |
| `started_at` | string | Момент старта прогона, UTC. Формат `str(datetime)`: `"YYYY-MM-DD HH:MM:SS.ffffff+00:00"`. |
| `finished_at` | string | Момент окончания прогона, тот же формат. |
| `tables` | array | Результаты посверенных таблиц (см. «Элемент tables[]»). Содержит и «ошибочные» записи: таблица, запрошенная в `tables` конфига, но отсутствующая хотя бы с одной стороны, попадает сюда с заполненным `error`. |
| `schema_diffs` | array | Различия схем — только таблицы, у которых они есть (см. «Элемент schema_diffs[]»). Пустой массив = схемы общих таблиц совпали. |
| `tables_only_in_source` | array<string> | Имена таблиц, которые есть только в источнике (в оригинальном регистре источника), отсортированы. |
| `tables_only_in_target` | array<string> | Имена таблиц только в приёмнике. |
| `config_summary` | object | Слепок настроек прогона (см. «config_summary»). Чувствительные значения опций (`password`, `passwd`, `secret`, `token`) маскируются строкой `"•••"`. |
| `equivalent` | bool | Итоговый вердикт: `true` ⇔ схемы чистые (`schema_diffs`, `tables_only_in_*` пусты) **и** у каждой таблицы `status == "ok"`. |
| `totals` | object | Агрегаты по всем таблицам (см. «totals»). |

## Элемент `tables[]`

Одна запись на таблицу. Счётчики строк — целые неотрицательные числа.

| Ключ | Тип | Семантика |
|---|---|---|
| `table` | string | Имя таблицы (в нижнем регистре — общий «логический» регистр сверки). |
| `pk` | array<string> | Колонки первичного ключа, по которым шёл merge (нижний регистр). Пустой массив — PK определить не удалось (см. `error`). |
| `src_rows` | int | Строк учтено со стороны источника (прочитано потоково + зачтено по хэш-сегментам). |
| `dst_rows` | int | Строк учтено со стороны приёмника. |
| `matched` | int | Строк, совпавших полностью (по нормализованным значениям). |
| `mismatched` | int | Строк с одинаковым PK, но разными значениями хотя бы в одной колонке. |
| `missing_in_target` | int | Строк источника, отсутствующих в приёмнике. |
| `extra_in_target` | int | Лишних строк приёмника (нет в источнике). |
| `duplicate_pk` | int | Дубликатов первичного ключа (по обеим сторонам суммарно). |
| `null_pk` | int | Строк с NULL в PK — merge по ним невозможен, считаются отдельной категорией расхождений. |
| `samples` | array | Примеры расхождений, не более `sample_limit` на таблицу (см. «Элемент samples[]»). |
| `column_mismatch_counts` | object | `{колонка: N}` — в скольких строках-`mismatch` разошлась эта колонка. Только колонки с N > 0. |
| `warnings` | array<string> | Человекочитаемые предупреждения (не влияют на `status`): коллации текстового PK, «продолжено с чекпоинта», «hash-режим недоступен (…)» и т.п. |
| `error` | string \| null | Текст ошибки, если таблицу сверить не удалось (нет PK, PK вне общих колонок, таблица отсутствует, ошибка БД после всех ретраев). При `error != null` счётчики недостоверны, `status == "error"`. |
| `duration_s` | float | Длительность сверки таблицы, секунды (округление до 3 знаков). |
| `mode` | string | Режим сверки: `"stream"` (потоковый merge) или `"hash"` (сегментные DB-side агрегаты + детализация разошедшихся сегментов). |
| `rows_hash_matched` | int | Строк зачтено эквивалентными по совпавшим хэш-сегментам без передачи данных. В stream-режиме `0`. |
| `rows_streamed` | int | Строк, детализированных потоково в hash-режиме (сумма src+dst по разошедшимся сегментам). В stream-режиме `0`. |
| `segments_matched` | int | Хэш-сегментов, совпавших целиком. В stream-режиме `0`. |
| `segments_streamed` | int | Хэш-сегментов, ушедших в потоковую детализацию. В stream-режиме `0`. |
| `total_diffs` | int | Сумма расхождений: `mismatched + missing_in_target + extra_in_target + duplicate_pk + null_pk`. |
| `status` | string | `"ok"` (расхождений нет), `"diff"` (есть расхождения), `"error"` (сверка не удалась). |
| `match_pct` | float | `matched / max(src_rows, dst_rows) * 100`, округление до 4 знаков; `100.0` для пустой таблицы. |

## Элемент `samples[]`

Пример одного расхождения. Единственный объект схемы v1 со **строго**
фиксированным набором ключей (ровно три, тест проверяет равенство):

| Ключ | Тип | Семантика |
|---|---|---|
| `kind` | string | Вид расхождения, одно из: `"missing_in_target"`, `"extra_in_target"`, `"mismatch"`, `"duplicate_pk"`, `"null_pk"`. |
| `pk` | array | Значения PK строки в отображаемом виде: значения приводятся к строке и обрезаются до 120 символов, `null` остаётся `null`. PK **не** маскируется даже при `mask_values: true`. |
| `columns` | object \| null | Только для `kind == "mismatch"`: `{колонка: [значение_источника, значение_приёмника]}`. Значения — отображаемые (строка ≤ 120 символов, `null`, либо `"•••"` при `mask_values: true`). Для остальных `kind` — `null`. |

## Элемент `schema_diffs[]`

| Ключ | Тип | Семантика |
|---|---|---|
| `table` | string | Имя таблицы. |
| `missing_in_target` | array<string> | Колонки, которых нет в приёмнике. |
| `extra_in_target` | array<string> | Лишние колонки приёмника. |
| `type_changes` | array | Смены типов: объекты `{column, source, target}` с именем колонки и «сырыми» типами движков. |
| `pk_mismatch` | object \| null | Различие первичных ключей: `{source: [...], target: [...]}` либо `null`. |

## `config_summary`

Слепок ключевых настроек прогона — для воспроизводимости и отображения
в отчёте. Состав может расширяться минорно (правило «игнорируйте
незнакомые ключи» действует и здесь).

| Ключ | Тип | Семантика |
|---|---|---|
| `source`, `target` | object | `{type, label, options}` эндпоинта; в `options` значения чувствительных ключей заменены на `"•••"`. |
| `rules` | object | Все правила нормализации (`NormalizeRules`, см. `docs/config-reference.md`). |
| `sample_limit` | int | Лимит примеров на таблицу. |
| `batch_size` | int | Размер чанка чтения. |
| `mask_values` | bool | Маскирование значений в samples. |
| `workers` | int | Число параллельных потоков. |
| `strategy` | string | `auto` \| `stream` \| `hash`. |
| `retry_attempts` | int | Попыток на таблицу. |
| `checkpoint` | bool | Был ли включён чекпоинт (сам путь не раскрывается). |

## `totals`

Агрегаты по всем элементам `tables[]` (суммы соответствующих счётчиков).

| Ключ | Тип | Семантика |
|---|---|---|
| `tables_total` | int | Всего посверенных таблиц (длина `tables`). |
| `tables_ok` | int | Таблиц со `status == "ok"`. |
| `src_rows`, `dst_rows` | int | Суммарные строки источника/приёмника. |
| `matched`, `mismatched`, `missing_in_target`, `extra_in_target`, `duplicate_pk`, `null_pk` | int | Суммы одноимённых счётчиков таблиц. |
| `total_diffs` | int | Сумма всех расхождений. |
| `match_pct` | float | `matched / max(src_rows, dst_rows) * 100`, округление до 4 знаков; `100.0` при нуле строк. |

## Пример отчёта

Реальный вывод демо-прогона (`dbparity demo`); `samples` сокращены
до пары элементов на таблицу.

```json
{
  "schema_version": 1,
  "source_label": "Oracle PROD (эмуляция)",
  "target_label": "PostgreSQL NEW",
  "started_at": "2026-07-02 21:19:57.637393+00:00",
  "finished_at": "2026-07-02 21:19:57.675259+00:00",
  "tables": [
    {
      "table": "customers",
      "pk": ["id"],
      "src_rows": 1200,
      "dst_rows": 1199,
      "matched": 1193,
      "mismatched": 4,
      "missing_in_target": 3,
      "extra_in_target": 2,
      "duplicate_pk": 0,
      "null_pk": 0,
      "samples": [
        {
          "kind": "mismatch",
          "pk": ["10"],
          "columns": {
            "name": ["Алексей Петрова", "Алексей Петрова (переименован)"]
          }
        },
        {
          "kind": "missing_in_target",
          "pk": ["101"],
          "columns": null
        }
      ],
      "column_mismatch_counts": {
        "name": 1,
        "balance": 1,
        "email": 1,
        "is_active": 1
      },
      "warnings": [],
      "error": null,
      "duration_s": 0.009,
      "mode": "stream",
      "rows_hash_matched": 0,
      "rows_streamed": 0,
      "segments_matched": 0,
      "segments_streamed": 0,
      "total_diffs": 9,
      "status": "diff",
      "match_pct": 99.4167
    },
    {
      "table": "orders",
      "pk": ["id"],
      "src_rows": 5000,
      "dst_rows": 5000,
      "matched": 4997,
      "mismatched": 3,
      "missing_in_target": 0,
      "extra_in_target": 0,
      "duplicate_pk": 0,
      "null_pk": 0,
      "samples": [
        {
          "kind": "mismatch",
          "pk": ["500"],
          "columns": {"amount": ["1890.0", "1890.02"]}
        }
      ],
      "column_mismatch_counts": {"amount": 2, "status": 1},
      "warnings": [],
      "error": null,
      "duration_s": 0.027,
      "mode": "stream",
      "rows_hash_matched": 0,
      "rows_streamed": 0,
      "segments_matched": 0,
      "segments_streamed": 0,
      "total_diffs": 3,
      "status": "diff",
      "match_pct": 99.94
    },
    {
      "table": "products",
      "pk": ["id"],
      "src_rows": 300,
      "dst_rows": 300,
      "matched": 300,
      "mismatched": 0,
      "missing_in_target": 0,
      "extra_in_target": 0,
      "duplicate_pk": 0,
      "null_pk": 0,
      "samples": [],
      "column_mismatch_counts": {},
      "warnings": [],
      "error": null,
      "duration_s": 0.001,
      "mode": "stream",
      "rows_hash_matched": 0,
      "rows_streamed": 0,
      "segments_matched": 0,
      "segments_streamed": 0,
      "total_diffs": 0,
      "status": "ok",
      "match_pct": 100.0
    }
  ],
  "schema_diffs": [
    {
      "table": "orders",
      "missing_in_target": ["discount"],
      "extra_in_target": [],
      "type_changes": [],
      "pk_mismatch": null
    }
  ],
  "tables_only_in_source": ["legacy_log"],
  "tables_only_in_target": ["audit_new"],
  "config_summary": {
    "source": {
      "type": "sqlite",
      "label": "Oracle PROD (эмуляция)",
      "options": {
        "path": "/tmp/dbparity_demo/source_oracle_like.db",
        "dialect_emulation": "oracle"
      }
    },
    "target": {
      "type": "sqlite",
      "label": "PostgreSQL NEW",
      "options": {"path": "/tmp/dbparity_demo/target_postgres_like.db"}
    },
    "rules": {
      "oracle_empty_string_is_null": true,
      "rtrim_strings": true,
      "unicode_nfc": true,
      "float_epsilon": 1e-09,
      "yn_as_bool": false,
      "truncate_time_if_midnight": false,
      "timestamp_precision": 6,
      "tz_to_utc": true,
      "bytes_as_md5": true
    },
    "sample_limit": 50,
    "batch_size": 5000,
    "mask_values": false,
    "workers": 1,
    "strategy": "auto",
    "retry_attempts": 1,
    "checkpoint": false
  },
  "equivalent": false,
  "totals": {
    "tables_total": 3,
    "tables_ok": 1,
    "src_rows": 6500,
    "dst_rows": 6499,
    "matched": 6490,
    "mismatched": 7,
    "missing_in_target": 3,
    "extra_in_target": 2,
    "duplicate_pk": 0,
    "null_pk": 0,
    "total_diffs": 12,
    "match_pct": 99.8462
  }
}
```

## История версий схемы

| `schema_version` | Изменения |
|---|---|
| 1 | Первая замороженная версия (DBParity 0.5.x). |
