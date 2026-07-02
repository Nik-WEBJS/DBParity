"""Демо-данные: две БД с заранее известными расхождениями.

Источник эмулирует Oracle (dialect_emulation), приёмник — «мигрированный
Postgres». Все расхождения детерминированы и описаны в EXPECTED —
на них же опираются интеграционные тесты (sqlite и live-PostgreSQL).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from ..config import Config, EndpointConfig, ReportConfig
from ..core.normalize import NormalizeRules

# Ожидаемые результаты демо-прогона (используются тестами)
EXPECTED = {
    "customers": {"src_rows": 1200, "dst_rows": 1199, "matched": 1193,
                  "mismatched": 4, "missing_in_target": 3, "extra_in_target": 2},
    "orders": {"src_rows": 5000, "dst_rows": 5000, "matched": 4997,
               "mismatched": 3, "missing_in_target": 0, "extra_in_target": 0},
    "products": {"src_rows": 300, "total_diffs": 0},
    "only_in_source": ["legacy_log"],
    "only_in_target": ["audit_new"],
    "schema_diffs": {"orders": {"missing_in_target": ["discount"]}},
}

_FIRST = ["Алексей", "Мария", "Иван", "Ольга", "Дмитрий", "Анна",
          "Сергей", "Елена", "Павел", "Наталья"]
_LAST = ["Иванов", "Петрова", "Сидоров", "Кузнецова", "Смирнов",
         "Попова", "Волков", "Соколова", "Морозов", "Новикова"]


def _customer(i: int) -> dict:
    name = f"{_FIRST[i % 10]} {_LAST[(i // 10) % 10]}"
    return {
        "id": i,
        "name": name,
        "email": f"user{i}@example.com",
        "balance": round(10 + (i * 7.13) % 9990, 2),
        "is_active": 0 if i % 5 == 0 else 1,
        "created_at": f"2025-{1 + i % 12:02d}-{1 + i % 28:02d}T{i % 24:02d}:00:00+00:00",
        "notes": "" if i % 7 == 0 else f"note {i}",   # '' в Oracle == NULL
    }


def _order(i: int) -> dict:
    return {
        "id": i,
        "customer_id": 1 + i % 1200,
        "amount": round(5 + (i * 3.77) % 4995, 2),
        "status": ["new", "paid", "shipped"][i % 3],
        "order_date": f"2025-{1 + i % 12:02d}-{1 + i % 27:02d}",
        "discount": round((i % 20) / 10, 1),
    }


def _product(i: int) -> dict:
    return {"id": i, "sku": f"SKU-{i:05d}", "title": f"Товар {i}",
            "price": round(1 + (i * 1.99) % 500, 2)}


# ---- генераторы строк (общие для sqlite-демо и live-PG теста) ---------------

def src_customer_rows() -> list:
    rows = []
    for i in range(1, 1201):
        c = _customer(i)
        if c["id"] == 60:   # ловушка: то же время, но в поясе +03:00
            c["created_at"] = "2025-03-01T12:00:00+03:00"
        if c["id"] == 70:   # ловушка: CHAR-паддинг пробелами
            c["name"] = c["name"] + "   "
        rows.append(c)
    return rows


def dst_customer_rows() -> list:
    rows = []
    for i in range(1, 1201):
        c = _customer(i)
        if c["id"] in (101, 102, 103):      # потеряны при миграции
            continue
        if c["id"] == 10:                    # реальные расхождения:
            c["name"] += " (переименован)"
        if c["id"] == 20:
            c["balance"] = round(c["balance"] + 0.01, 2)
        if c["id"] == 30:
            c["email"] = "changed30@example.com"
        if c["id"] == 40:
            c["is_active"] = 1 - c["is_active"]
        if c["id"] == 60:                    # ловушки (расхождением НЕ являются):
            c["created_at"] = "2025-03-01T09:00:00+00:00"
        if c["notes"] == "":                 # Postgres хранит честный NULL
            c["notes"] = None
        rows.append(c)
    rows.append({**_customer(2001), "notes": "note 2001"})   # лишние строки
    rows.append({**_customer(2002), "notes": "note 2002"})
    return rows


def src_order_rows() -> list:
    return [_order(i) for i in range(1, 5001)]


def dst_order_rows() -> list:
    """Без колонки discount («потерялась» при миграции) + 3 расхождения."""
    rows = []
    for o in src_order_rows():
        o = {k: v for k, v in o.items() if k != "discount"}
        if o["id"] in (500, 1500):
            o["amount"] = round(o["amount"] + 0.02, 2)
        if o["id"] == 2500:
            o["status"] = "refunded"
        rows.append(o)
    return rows


def product_rows() -> list:
    return [_product(i) for i in range(1, 301)]


# ---- сборка демо-БД ----------------------------------------------------------

def build_demo(outdir: str | Path) -> Config:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    src_path = out / "source_oracle_like.db"
    dst_path = out / "target_postgres_like.db"
    for p in (src_path, dst_path):
        if p.exists():
            p.unlink()

    # ---- ИСТОЧНИК («Oracle») ------------------------------------------------
    src = sqlite3.connect(src_path)
    src.executescript("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY, name TEXT, email TEXT, balance REAL,
            is_active INTEGER, created_at TIMESTAMP, notes TEXT);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL,
            status TEXT, order_date DATE, discount REAL);
        CREATE TABLE products (
            id INTEGER PRIMARY KEY, sku TEXT, title TEXT, price REAL);
        CREATE TABLE legacy_log (id INTEGER PRIMARY KEY, msg TEXT);
    """)
    src.executemany(
        "INSERT INTO customers VALUES (:id,:name,:email,:balance,:is_active,:created_at,:notes)",
        src_customer_rows())
    src.executemany(
        "INSERT INTO orders VALUES (:id,:customer_id,:amount,:status,:order_date,:discount)",
        src_order_rows())
    src.executemany("INSERT INTO products VALUES (:id,:sku,:title,:price)", product_rows())
    src.executemany("INSERT INTO legacy_log VALUES (?, ?)",
                    [(i, f"legacy {i}") for i in range(1, 51)])
    src.commit()
    src.close()

    # ---- ПРИЁМНИК («PostgreSQL после миграции») -----------------------------
    dst = sqlite3.connect(dst_path)
    dst.executescript("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY, name TEXT, email TEXT, balance REAL,
            is_active INTEGER, created_at TIMESTAMP, notes TEXT);
        CREATE TABLE orders (  -- колонка discount «потерялась» при миграции
            id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL,
            status TEXT, order_date DATE);
        CREATE TABLE products (
            id INTEGER PRIMARY KEY, sku TEXT, title TEXT, price REAL);
        CREATE TABLE audit_new (id INTEGER PRIMARY KEY, action TEXT);
    """)
    dst.executemany(
        "INSERT INTO customers VALUES (:id,:name,:email,:balance,:is_active,:created_at,:notes)",
        dst_customer_rows())
    dst.executemany(
        "INSERT INTO orders VALUES (:id,:customer_id,:amount,:status,:order_date)",
        dst_order_rows())
    dst.executemany("INSERT INTO products VALUES (:id,:sku,:title,:price)", product_rows())
    dst.executemany("INSERT INTO audit_new VALUES (?, ?)",
                    [(i, f"migrated batch {i}") for i in range(1, 11)])
    dst.commit()
    dst.close()

    # ---- Конфиг -------------------------------------------------------------
    cfg = Config(
        source=EndpointConfig(type="sqlite", label="Oracle PROD (эмуляция)",
                              options={"path": str(src_path),
                                       "dialect_emulation": "oracle"}),
        target=EndpointConfig(type="sqlite", label="PostgreSQL NEW",
                              options={"path": str(dst_path)}),
        rules=NormalizeRules(rtrim_strings=True),
        report=ReportConfig(html=str(out / "dbparity_report.html"),
                            json=str(out / "dbparity_report.json")),
    )
    # YAML-версия конфига — как образец для реальных прогонов
    (out / "demo_config.yaml").write_text(yaml.safe_dump({
        "source": {"type": "sqlite", "label": "Oracle PROD (эмуляция)",
                   "path": str(src_path), "dialect_emulation": "oracle"},
        "target": {"type": "sqlite", "label": "PostgreSQL NEW",
                   "path": str(dst_path)},
        "rules": {"rtrim_strings": True},
        "report": {"html": str(out / "dbparity_report.html"),
                   "json": str(out / "dbparity_report.json")},
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return cfg
