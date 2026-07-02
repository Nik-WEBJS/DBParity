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
- [ ] MSSQL: полноценный адаптер + docker-тест в CI (mcr mssql-server)
- [x] Бинарная сортировка для текстовых PK: `COLLATE "C"` (PG) /
  `NLSSORT BINARY` (Oracle) / `COLLATE BINARY` (sqlite) /
  `Latin1_General_BIN2` (MSSQL) — предупреждение заменено гарантией

## v0.5 — Parallel-run mode

- [ ] Непрерывная сверка во время dual-write перед переключением трафика:
  окно по watermark-колонке, инкрементальные прогоны, отчёт-таймлайн

## v0.9 — Release candidate

- [ ] Стабилизация формата config.yaml и JSON-отчёта (semver-гарантии)
- [x] `dbparity validate` — проверка конфига без подключения к БД,
  агрегированные ошибки с подсказками опечаток (сделано досрочно)
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
