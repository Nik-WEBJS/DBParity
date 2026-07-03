"""Веб-консоль: запуск сверки через HTTP, отдача отчётов, безопасность."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from dbparity.demo.seed import build_demo
from dbparity.web import create_server


@pytest.fixture()
def console(tmp_path):
    srv = create_server("127.0.0.1", 0, tmp_path / "workdir")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.port}"
    yield base, srv
    srv.shutdown()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8")


def _post(url: str, payload: dict):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_index_page(console):
    base, _ = console
    status, body = _get(base + "/")
    assert status == 200
    assert "config.yaml" in body and "Запустить сверку" in body


def test_run_rejects_missing_config(console):
    base, _ = console
    status, body = _post(base + "/api/run", {"config_path": "/nope/nowhere.yaml"})
    assert status == 400
    assert "не найден" in body["error"]


def test_full_run_and_report(console, tmp_path):
    base, _ = console
    build_demo(tmp_path / "demo")            # создаёт demo_config.yaml
    cfg_path = tmp_path / "demo" / "demo_config.yaml"

    status, body = _post(base + "/api/run", {"config_path": str(cfg_path)})
    assert status == 200
    rid = body["id"]

    deadline = time.time() + 30
    run = None
    while time.time() < deadline:
        _, raw = _get(base + "/api/runs")
        run = next(r for r in json.loads(raw) if r["id"] == rid)
        if run["status"] != "running":
            break
        time.sleep(0.3)
    assert run is not None and run["status"] == "done", run
    assert run["equivalent"] is False        # демо содержит 12 расхождений
    assert run["total_diffs"] == 12

    status, report = _get(base + f"/runs/{rid}/report.html")
    assert status == 200 and "НЕ ЭКВИВАЛЕНТНО" in report
    status, rjson = _get(base + f"/runs/{rid}/report.json")
    assert status == 200
    assert json.loads(rjson)["schema_version"] == 1


def test_remote_bind_requires_opt_in(tmp_path):
    """Бинд не на localhost без явного opt-in запрещён (нет аутентификации)."""
    with pytest.raises(ValueError):
        create_server("0.0.0.0", 0, tmp_path / "w")
    srv = create_server("0.0.0.0", 0, tmp_path / "w2", allow_remote=True)
    srv.server_close()


def test_oversized_body_rejected(console):
    """Тело больше потолка отклоняется до чтения (DoS-защита)."""
    import http.client
    base, srv = console
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    try:
        conn.request("POST", "/api/run", body=b"x" * (2 * 1024 * 1024),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 413
    except (ConnectionError, OSError):
        pass    # сервер оборвал приём гигантского тела — тоже защита
    finally:
        conn.close()
    assert srv.runs_snapshot() == []         # запуск не создан


def test_path_traversal_blocked(console):
    base, _ = console
    for url in ("/runs/../../etc/passwd", "/runs/1x/report.html",
                "/runs/999/report.html"):
        try:
            status, _body = _get(base + url)
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 404, url
