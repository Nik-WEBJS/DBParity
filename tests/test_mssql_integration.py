"""Live integration with Microsoft SQL Server (when DBPARITY_MSSQL_DSN is set).

In CI the server is an mcr.microsoft.com/mssql/server:2022-latest container;
locally, any SQL Server works, e.g.:
`docker run -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD='DbParity!Passw0rd' \
    -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest`
and
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
    not DSN, reason="DBPARITY_MSSQL_DSN not set - requires a live SQL Server")


@pytest.fixture()
def mssql_target():
    pyodbc = pytest.importorskip("pyodbc")
    conn = pyodbc.connect(DSN, autocommit=True)
    cur = conn.cursor()
    for t in ("customers", "orders", "products", "audit_new"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    # created_at is datetimeoffset: seed emits ISO strings with '+00:00',
    # SQL Server parses them implicitly (the 'T' format is locale-independent),
    # the adapter reads them via an output converter as aware datetimes
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
    # close BEFORE yield: engine.run() opens its own connections
    conn.close()
    yield


def test_sqlite_source_to_live_mssql(mssql_target, tmp_path):
    demo_cfg = seed.build_demo(tmp_path)          # sqlite source ("Oracle" stand-in)
    cfg = Config(
        source=demo_cfg.source,
        target=EndpointConfig(
            type="mssql", label="SQL Server LIVE",
            options={"dsn": DSN}),
        rules=NormalizeRules(rtrim_strings=True, truncate_time_if_midnight=True),
    )
    run = engine.run(cfg)
    by = {t.table: t for t in run.tables}

    # the same expected counters as in the sqlite demo
    for key, exp in seed.EXPECTED["customers"].items():
        assert getattr(by["customers"], key) == exp, f"customers.{key}"
    for key, exp in seed.EXPECTED["orders"].items():
        assert getattr(by["orders"], key) == exp, f"orders.{key}"
    # an errored table also has total_diffs == 0 - check error and src_rows
    # so the hash path (products under the auto strategy) cannot hide a failure
    assert by["products"].error is None, by["products"].error
    assert by["products"].total_diffs == 0
    assert by["products"].src_rows == 300

    assert run.tables_only_in_source == ["legacy_log"]
    assert run.tables_only_in_target == ["audit_new"]

    # type changes (real->decimal, int->bit) are visible in the schema...
    sd = {d.table: d for d in run.schema_diffs}
    assert "customers" in sd
    changed = {c["column"] for c in sd["customers"].type_changes}
    assert "is_active" in changed
    # ...but produce no false DATA diffs (verified by the counters above)
    assert not run.equivalent


def test_hash_mode_sqlite_to_live_mssql(tmp_path):
    """Cross-engine segment hashes: sqlite canonicalization == MSSQL zero trimming."""
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
    # qty decimal(12,2): 100 is stored as 100.00 - trailing-zero trimming
    # must reduce it to '100' and match the sqlite canonicalization
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

    # Sanity check BEFORE the run: a DDL-visibility truth table across all
    # combinations (autocommit x name style) - CI used to hit 42S02 only on
    # the adapter's sessions, and the matrix names the guilty combination.
    import time as _time
    combos, db = {}, None
    for _ in range(20):
        results = {}
        for ac in (True, False):
            chk = pyodbc.connect(DSN, autocommit=ac)
            ccur = chk.cursor()
            db = ccur.execute("SELECT DB_NAME()").fetchone()[0]
            for label, sql in (
                    ("plain", "SELECT COUNT(*) FROM dbo.hnums"),
                    ("bracket", "SELECT COUNT(*) FROM [dbo].[hnums]")):
                try:
                    results[f"ac={ac}/{label}"] = (
                        ccur.execute(sql).fetchone()[0])
                except pyodbc.Error as e:
                    results[f"ac={ac}/{label}"] = f"ERR {e.args[0]}"
            chk.close()
        combos = results
        if all(v == n for v in combos.values()):
            break
        _time.sleep(0.5)
    assert all(v == n for v in combos.values()), (
        f"seed visibility matrix (DB_NAME()={db!r}): {combos}")

    cfg = Config(
        source=EndpointConfig("sqlite", "src", {"path": str(src_p)}),
        target=EndpointConfig("mssql", "SQL Server LIVE",
                              options={"dsn": DSN}),
        strategy="hash",
        hash_leaf_rows=256,
        retry_attempts=2,        # a safeguard against first-session transients
        retry_backoff_s=0.5,
    )
    run = engine.run(cfg)
    t = run.tables[0]
    # self-diagnostics: if T-SQL fails or the table is ineligible, the cause
    # is in error/warnings - surface it in the assert message
    assert t.error is None, f"table failed: {t.error}"
    assert t.mode == "hash", f"mode={t.mode}; warnings={t.warnings}"
    assert t.mismatched == 2
    assert t.missing_in_target == 0 and t.extra_in_target == 0
    assert t.matched == n - 2
    # most rows are settled by aggregates, with no network transfer
    assert t.rows_hash_matched > n * 0.7
    assert t.rows_streamed < n
