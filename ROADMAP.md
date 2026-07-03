# Роадмап до v1.0

Философия: dbparity — инструмент *доказательства*, поэтому приоритет всегда
корректность → масштаб → удобство. Ложное «ЭКВИВАЛЕНТНО» — худший баг проекта.

## v0.2 — Correctness & Scale basics

- [x] Oracle: NUMBER → Decimal (а не float!) и LOB → значения — без этого
  верификатор сам терял бы точность
- [x] NULL в PK: отдельная категория `null_pk` вместо недетерминированного merge
- [x] Текстовые PK: предупреждение о различиях сортировки/коллаций между движками
- [x] Параллельная сверка таблиц (`workers: N`, соединение на поток)
- [x] Живой прогресс в CLI
- [x] Workflow публикации на PyPI (по тегу `v*`)

## v0.3 — Big tables (100M+ строк)

- [x] Бакетные DB-side хэши за один скан (GROUP BY по PK-диапазонам),
  потоковая детализация только расходящихся бакетов; `strategy: auto|stream|hash`.
  Типы вне hash-набора (float/datetime/bytes) → авто-fallback в stream.
  Несовершенная канонизация деградирует скорость, но не корректность
- [x] Checkpoint/resume: атомарный JSON-стейт (fingerprint конфига,
  watermark по PK, партиал-слот на таблицу), `--resume` в CLI
- [x] Retry на сетевые ошибки: `retry_attempts`/`retry_backoff_s`,
  свежая пара соединений на попытку, продолжение с последнего watermark
- [x] Бенчмарк-матрица в CI: `bench --json` + workflow с порогами регрессии
  и публикацией метрик в summary

## v0.4 — Oracle/MSSQL hardening

- [ ] Обкатка на реальных Oracle-инстансах (issues от сообщества)
- [ ] Кодировки: AL32UTF8 vs UTF-8 edge cases, NCHAR/NVARCHAR2
- [x] MSSQL: полноценный адаптер (ODBC 18, datetimeoffset-конвертер,
  digest-API с T-SQL канонизацией) + live-джоба в CI (mcr mssql-server:2022)
- [x] Бинарная сортировка для текстовых PK: `COLLATE "C"` (PG) /
  `NLSSORT BINARY` (Oracle) / `COLLATE BINARY` (sqlite) /
  `Latin1_General_BIN2` (MSSQL) — предупреждение заменено гарантией

## v0.5 — Parallel-run mode

- [x] Инкрементальные прогоны по watermark-колонке (`incremental:` в конфиге,
  `--full` для сброса): сверяются только изменённые строки, missing/extra
  среди них = дрейф dual-write; стейт с fingerprint конфига
- [x] Отчёт-таймлайн серии инкрементальных прогонов: журнал в стейте,
  `dbparity history` (rich-таблица + HTML с line-chart дрейфа «до нуля»)
- [ ] Режим наблюдения: запуск по расписанию до достижения нуля дрейфа
  (пока — cron + `dbparity history`)

## v0.9 — Release candidate

- [x] Стабилизация формата JSON-отчёта: schema_version=1, правила эволюции,
  справочники docs/report-format.md и docs/config-reference.md,
  золотой тест формата
- [x] `dbparity validate` — проверка конфига без подключения к БД,
  агрегированные ошибки с подсказками опечаток (сделано досрочно)
- [x] Веб-консоль `dbparity serve`: локальный UI (stdlib-only) — запуск
  сверок из браузера, live-прогресс, раздача отчётов
- [ ] Документация: сайт (mkdocs), рецепты для типовых миграций
  (Oracle→PG, MSSQL→PG, включая СНГ-специфику Postgres Pro)

## Критерии v1.0

1. ≥5 реальных миграций проверено сообществом/автором, из них ≥1 с таблицей 100M+
2. Ноль известных классов ложных «ЭКВИВАЛЕНТНО»
3. Oracle и MSSQL адаптеры покрыты интеграционными тестами в CI
4. Форматы config/отчётов заморожены (breaking changes → v2)
5. Публикация на PyPI, установка `pip install dbparity`

## Как релизить

Тег `vX.Y.Z` на main → CI собирает и публикует на PyPI
(требуется одноразовая настройка Trusted Publisher на pypi.org:
проект dbparity → Publishing → GitHub → repo `Nik-WEBJS/DBParity`,
workflow `release.yml`, environment `pypi`).
