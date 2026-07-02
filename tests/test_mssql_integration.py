"""Live-интеграция с Microsoft SQL Server (при заданном DBPARITY_MSSQL_DSN).

В CI сервером выступает контейнер mcr.microsoft.com/mssql/server:2022-latest;
локально — любой SQL Server, например:
`docker run -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD='DbParity!Passw0rd' \
    -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest`
и
DBPARITY_MSSQL_DSN="Driver={ODBC Driver 18 for SQL Server};\
Server=127.0.0.1,1433;Database=master;UID=sa;PWD=DbParity!Passw0rd;\
TrustServerCertificate=yes" pytest tests/test_mssql_integration.py
"""
import os
import sqlite3

import pytest

from dbparity.config import Config, EndpointConfig
from dbparity.core import engine
from dbparity.core.normalize import NormalizeRules
from dbparity.demo import seed

DSN = os.environ.get("DBPARITY_MSSQL_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="DBPARITY_MSSQL_DSN не задан — нужен живой SQL Server")


@pytest.fixture()
def mssql_target():
    pyodbc = pytest.importorskip("pyodbc")
    conn = pyodbc.connect(DSN, autocommit=True)
    cur = conn.cursor()
    for t in ("customers", "orders", "products", "audit_new"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    # created_at — datetimeoffset: seed отдаёт ISO-строки с '+00:00',
    # SQL Server парсит их неявно (формат с 'T' локале-независим),
    # адаптер читает через output-конвертер как aware datetime
    cur.execute("""
        CREATE TABLE customers (
            id int PRIMARY KEY, name nvarchar(200), email nvarchar(200),
            balance decimal(12,2), is_active bit,
            created_at datetimeoffset, notes nvarchar(200))""")
    cur.execute("""
        CREATE TABLE orders (
            id int PRIMARY KEY, customer_id int,
            amount decimal(12,2), status nvarchar(20), order_date date)""")
    cur.execute("""
        CREATE TABLE products (
            id int PRIMARY KEY, sku nvarchar(40), title nvarchar(100),
            price decimal(12,2))""")
    cur.execute("CREATE TABLE audit_new (id int PRIMARY KEY, action nvarchar(100))")

    cur.executemany(
        "INSERT INTO customers VALUES (?,?,?,?,?,?,?)",
        [(c["id"], c["name"], c["email"], c["balance"], bool(c["is_active"]),
          c["created_at"], c["notes"]) for c in seed.dst_customer_rows()])
    cur.executemany(
        "INSERT INTO orders VALUES (?,?,?,?,?)",
        [(o["id"], o["customer_id"], o["amount"], o["status"], o["order_date"])
         for o in seed.dst_order_rows()])
    cur.executemany(
        "INSERT INTO products VALUES (?,?,?,?)",
        [(p["id"], p["sku"], p["title"], p["price"])
         for p in seed.product_rows()])
    cur.executemany("INSERT INTO audit_new VALUES (?,?)",
                    [(i, f"migrated batch {i}") for i in range(1, 11)])
    # закрываем ДО yield: engine.run() откроет свои соединения
    conn.close()
    yield


def test_sqlite_source_to_live_mssql(mssql_target, tmp_path):
    demo_cfg = seed.build_demo(tmp_path)          # sqlite-источник («Oracle»)
    cfg = Config(
        source=demo_cfg.source,
        target=EndpointConfig(
            type="mssql", label="SQL Server LIVE",
            options={"dsn": DSN}),
        rules=NormalizeRules(rtrim_strings=True, truncate_time_if_midnight=True),
    )
    run = engine.run(cfg)
    by = {t.table: t for t in run.tables}

    # те же ожидаемые счётчики, что и в sqlite-демо
    for key, exp in seed.EXPECTED["customers"].items():
        assert getattr(by["customers"], key) == exp, f"customers.{key}"
    for key, exp in seed.EXPECTED["orders"].items():
        assert getattr(by["orders"], key) == exp, f"orders.{key}"
    assert by["products"].total_diffs == 0

    assert run.tables_only_in_source == ["legacy_log"]
    assert run.tables_only_in_target == ["audit_new"]

    # смена типов (real→decimal, int→bit) видна в схеме…
    sd = {d.table: d for d in run.schema_diffs}
    assert "customers" in sd
    changed = {c["column"] for c in sd["customers"].type_changes}
    assert "is_active" in changed
    # …но ложных расхождений ДАННЫХ не создаёт (проверено счётчиками выше)
    assert not run.equivalent


def test_hash_mode_sqlite_to_live_mssql(tmp_path):
    """Кросс-движковые сегментные хэши: sqlite-канонизация == MSSQL-срез нулей."""
    pyodbc = pytest.importorskip("pyodbc")
    n = 3000
    rows = [(i, f"item {i}", 100 if i % 10 == 0 else i % 47,
             round((i % 50) + 0.5, 2)) for i in range(1, n + 1)]

    src_p = tmp_path / "hsrc.db"
    conn = sqlite3.connect(src_p)
    conn.execute("CREATE TABLE hnums (id INTEGER PRIMARY KEY, name TEXT,"
                 " qty INTEGER, price NUMERIC)")
    conn.executemany("INSERT INTO hnums VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()

    ms = pyodbc.connect(DSN, autocommit=True)
    cur = ms.cursor()
    cur.execute("DROP TABLE IF EXISTS hnums")
    # qty decimal(12,2): 100 хранится как 100.00 — срез хвостовых нулей
    # обязан свести к '100' и совпасть с канонизацией sqlite
    cur.execute("CREATE TABLE hnums (id int PRIMARY KEY, name varchar(100),"
                " qty decimal(12,2), price decimal(12,2))")
    dst_rows = []
    for r in rows:
        r = list(r)
        if r[0] == 777:
            r[1] = "changed"
        if r[0] == 2000:
            r[2] = r[2] + 1
        dst_rows.append(tuple(r))
    cur.executemany("INSERT INTO hnums VALUES (?,?,?,?)", dst_rows)
    ms.close()

    cfg = Config(
        source=EndpointConfig("sqlite", "src", {"path": str(src_p)}),
        target=EndpointConfig("mssql", "SQL Server LIVE",
                              options={"dsn": DSN}),
        strategy="hash",
        hash_leaf_rows=256,
    )
    run = engine.run(cfg)
    t = run.tables[0]
    # самодиагностика: при падении T-SQL или неэлигибельности причина
    # лежит в error/warnings — выводим её в сообщении assert
    assert t.error is None, f"таблица упала: {t.error}"
    assert t.mode == "hash", f"mode={t.mode}; warnings={t.warnings}"
    assert t.mismatched == 2
    assert t.missing_in_target == 0 and t.extra_in_target == 0
    assert t.matched == n - 2
    # большинство строк зачтено агрегатами, без передачи по сети
    assert t.rows_hash_matched > n * 0.7
    assert t.rows_streamed < n
