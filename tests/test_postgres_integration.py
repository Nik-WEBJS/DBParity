"""Live-интеграция с PostgreSQL (запускается при заданном DBPARITY_PG_DSN).

В песочнице сервером выступает PGlite (Postgres в WASM) через pglite-socket;
локально — любой PostgreSQL: `docker compose up -d` и
`DBPARITY_PG_DSN="host=127.0.0.1 dbname=dbparity user=postgres password=dbparity" pytest`.
"""
import os
import sqlite3

import pytest

from dbparity.config import Config, EndpointConfig
from dbparity.core import engine
from dbparity.core.normalize import NormalizeRules
from dbparity.demo import seed

DSN = os.environ.get("DBPARITY_PG_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="DBPARITY_PG_DSN не задан — нужен живой PostgreSQL")


@pytest.fixture()
def pg_target():
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(DSN, autocommit=True)
    conn.prepare_threshold = None       # PGlite: одна сессия на все коннекты
    cur = conn.cursor()
    cur.execute("DEALLOCATE ALL")
    for t in ("customers", "orders", "products", "audit_new"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    cur.execute("""
        CREATE TABLE customers (
            id integer PRIMARY KEY, name text, email text,
            balance numeric(12,2), is_active boolean,
            created_at timestamptz, notes text)""")
    cur.execute("""
        CREATE TABLE orders (
            id integer PRIMARY KEY, customer_id integer,
            amount numeric(12,2), status text, order_date date)""")
    cur.execute("""
        CREATE TABLE products (
            id integer PRIMARY KEY, sku text, title text, price numeric(12,2))""")
    cur.execute("CREATE TABLE audit_new (id integer PRIMARY KEY, action text)")

    customers = [dict(c, is_active=bool(c["is_active"]))
                 for c in seed.dst_customer_rows()]
    cur.executemany(
        "INSERT INTO customers VALUES (%(id)s,%(name)s,%(email)s,%(balance)s,"
        "%(is_active)s,%(created_at)s,%(notes)s)", customers)
    cur.executemany(
        "INSERT INTO orders VALUES (%(id)s,%(customer_id)s,%(amount)s,"
        "%(status)s,%(order_date)s)", seed.dst_order_rows())
    cur.executemany(
        "INSERT INTO products VALUES (%(id)s,%(sku)s,%(title)s,%(price)s)",
        seed.product_rows())
    cur.executemany("INSERT INTO audit_new VALUES (%s,%s)",
                    [(i, f"migrated batch {i}") for i in range(1, 11)])
    # закрываем ДО yield: PGlite-socket держит одно соединение за раз,
    # а engine.run() откроет своё
    conn.close()
    yield


def test_sqlite_source_to_live_postgres(pg_target, tmp_path):
    demo_cfg = seed.build_demo(tmp_path)          # sqlite-источник («Oracle»)
    cfg = Config(
        source=demo_cfg.source,
        target=EndpointConfig(
            type="postgres", label="PostgreSQL LIVE",
            options={"dsn": DSN,
                     "server_side":
                         os.environ.get("DBPARITY_PG_SERVER_SIDE", "1") == "1"}),
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

    # смена типов (real→numeric, int→boolean) видна в схеме…
    sd = {d.table: d for d in run.schema_diffs}
    assert "customers" in sd
    changed = {c["column"] for c in sd["customers"].type_changes}
    assert "is_active" in changed
    # …но ложных расхождений ДАННЫХ не создаёт (проверено счётчиками выше)
    assert not run.equivalent


def test_hash_mode_sqlite_to_live_postgres(tmp_path):
    """Кросс-движковые сегментные хэши: sqlite-канонизация == PG trim_scale."""
    psycopg = pytest.importorskip("psycopg")
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

    pg = psycopg.connect(DSN, autocommit=True)
    pg.prepare_threshold = None         # PGlite: одна сессия на все коннекты
    cur = pg.cursor()
    cur.execute("DEALLOCATE ALL")
    cur.execute("DROP TABLE IF EXISTS hnums")
    # qty numeric(12,2): 100 хранится как 100.00 — trim_scale обязан
    # свести к '100' и совпасть с канонизацией sqlite
    cur.execute("CREATE TABLE hnums (id integer PRIMARY KEY, name text,"
                " qty numeric(12,2), price numeric(12,2))")
    dst_rows = []
    for r in rows:
        r = list(r)
        if r[0] == 777:
            r[1] = "changed"
        if r[0] == 2000:
            r[2] = r[2] + 1
        dst_rows.append(tuple(r))
    cur.executemany("INSERT INTO hnums VALUES (%s,%s,%s,%s)", dst_rows)
    pg.close()

    cfg = Config(
        source=EndpointConfig("sqlite", "src", {"path": str(src_p)}),
        target=EndpointConfig(
            "postgres", "PostgreSQL LIVE",
            options={"dsn": DSN,
                     "prepare_threshold": None,
                     "server_side":
                         os.environ.get("DBPARITY_PG_SERVER_SIDE", "1") == "1"}),
        strategy="hash",
        hash_leaf_rows=256,
    )
    run = engine.run(cfg)
    t = run.tables[0]
    assert t.mode == "hash"
    assert t.mismatched == 2
    assert t.missing_in_target == 0 and t.extra_in_target == 0
    assert t.matched == n - 2
    # большинство строк зачтено агрегатами, без передачи по сети
    assert t.rows_hash_matched > n * 0.7
    assert t.rows_streamed < n
