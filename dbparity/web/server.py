"""Local web console on top of the core (stdlib-only, no flask/fastapi).

`python -m dbparity.web` (or `dbparity serve`): a page with a
"path to config.yaml" form, comparison runs in background threads,
live progress via polling, and serving of the finished HTML/JSON reports.

Security: the tool is LOCAL — it listens on 127.0.0.1; files are served
only via internal paths from the runs dict (no path traversal from the
URL); user strings are escaped with html.escape. Report paths from the
config are ignored — the console writes reports only into its workdir.
"""
from __future__ import annotations

import html
import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..config import load_config
from ..core import engine
from ..report.render import write_html, write_json

_RUN_URL = re.compile(r"^/runs/(\d+)/report\.(html|json)$")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_MAX_BODY = 1_000_000       # request body ceiling (DoS protection)


class ConsoleServer(ThreadingHTTPServer):
    """The console HTTP server; run state lives in process memory."""

    daemon_threads = True

    def __init__(self, host: str, port: int, workdir,
                 allow_remote: bool = False):
        if host not in _LOOPBACK and not allow_remote:
            raise ValueError(
                f"The console has no authentication: binding to {host!r} "
                "would let anyone on the network run comparisons with "
                "arbitrary configs (reading local files, outbound "
                "connections). Keep 127.0.0.1 or explicitly accept the "
                "risk with the --allow-remote flag.")
        super().__init__((host, port), _Handler)
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.runs: dict = {}
        self._next_id = 1

    @property
    def port(self) -> int:
        return self.server_address[1]

    # ---- runs ---------------------------------------------------------------

    def start_run(self, config_path: str) -> int:
        """Validates the config and starts a comparison in the background; ValueError → 400."""
        p = Path(config_path).expanduser()
        if not p.exists():
            raise ValueError(f"file not found: {p}")
        cfg = load_config(p)                 # ValueError with the problem text
        with self.lock:
            rid = self._next_id
            self._next_id += 1
            rdir = self.workdir / f"run_{rid}"
            self.runs[rid] = {
                "id": rid,
                "status": "running",
                "config_path": str(p),
                "started": datetime.now(timezone.utc).isoformat(),
                "progress": {},
                "equivalent": None,
                "total_diffs": None,
                "error": None,
                "report_html": None,
                "report_json": None,
                "_dir": rdir,
            }

        def on_progress(table: str, n: int) -> None:
            with self.lock:
                self.runs[rid]["progress"][table] = n

        def worker() -> None:
            try:
                # reports go only into the console workdir (config paths are ignored)
                cfg.report.html = None
                cfg.report.json = None
                run = engine.run(cfg, on_progress=on_progress)
                rdir.mkdir(parents=True, exist_ok=True)
                html_p = write_html(run, rdir / "report.html")
                json_p = write_json(run, rdir / "report.json")
                with self.lock:
                    r = self.runs[rid]
                    r["status"] = "done"
                    r["equivalent"] = run.equivalent
                    r["total_diffs"] = run.totals["total_diffs"]
                    r["report_html"] = str(html_p)
                    r["report_json"] = str(json_p)
            except Exception as e:  # noqa: BLE001 — status goes to the UI
                with self.lock:
                    self.runs[rid]["status"] = "error"
                    self.runs[rid]["error"] = f"{type(e).__name__}: {e}"

        threading.Thread(target=worker, daemon=True).start()
        return rid

    def runs_snapshot(self) -> list:
        with self.lock:
            out = []
            for r in sorted(self.runs.values(), key=lambda x: -x["id"]):
                out.append({k: v for k, v in r.items() if not k.startswith("_")})
            return out

    def report_path(self, rid: int, kind: str):
        with self.lock:
            r = self.runs.get(rid)
        if not r:
            return None
        return r.get(f"report_{kind}")


class _Handler(BaseHTTPRequestHandler):
    server: ConsoleServer

    def log_message(self, *args) -> None:   # noqa: D102 — keep the console quiet
        pass

    # ---- helpers ------------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ---- routes ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — stdlib API
        if self.path == "/" or self.path.startswith("/?"):
            self._send(200, _INDEX_HTML.encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        if self.path == "/api/runs":
            self._json(200, self.server.runs_snapshot())
            return
        m = _RUN_URL.match(self.path)
        if m:
            path = self.server.report_path(int(m.group(1)), m.group(2))
            if path and Path(path).exists():
                ctype = ("text/html; charset=utf-8" if m.group(2) == "html"
                         else "application/json; charset=utf-8")
                self._send(200, Path(path).read_bytes(), ctype)
                return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/run":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = -1
        if length < 0 or length > _MAX_BODY:
            self._json(413, {"error": "request body too large"})
            self.close_connection = True
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            config_path = str(data.get("config_path", "")).strip()
            if not config_path:
                raise ValueError("provide a path to config.yaml")
            rid = self.server.start_run(config_path)
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"error": html.escape(str(e))})
            return
        self._json(200, {"id": rid})


def create_server(host: str = "127.0.0.1", port: int = 8765,
                  workdir="dbparity_console",
                  allow_remote: bool = False) -> ConsoleServer:
    return ConsoleServer(host, port, workdir, allow_remote=allow_remote)


# ---------------------------------------------------------------------------
# The console page: Tabler CDN + vanilla JS polling /api/runs.
# All data comes from the API and is inserted via textContent (no XSS).
# ---------------------------------------------------------------------------

_INDEX_HTML = """<!doctype html>
<html lang="en" data-bs-theme="light">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>DBParity — console</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/core@1.4.0/dist/css/tabler.min.css"/>
</head>
<body>
<div class="page">
  <header class="navbar navbar-expand-md">
    <div class="container-xl">
      <h1 class="navbar-brand mb-0">
        <span class="badge bg-blue text-white me-2">DB</span>Parity · console
      </h1>
      <span class="text-secondary">local tool — do not expose to the network</span>
    </div>
  </header>
  <div class="page-wrapper"><div class="page-body"><div class="container-xl">

    <div class="card mb-3"><div class="card-body">
      <div class="row g-2">
        <div class="col">
          <input id="cfg" class="form-control" placeholder="path to config.yaml"/>
        </div>
        <div class="col-auto">
          <button id="go" class="btn btn-primary">Run comparison</button>
        </div>
      </div>
      <div id="err" class="text-danger mt-2" style="display:none"></div>
    </div></div>

    <div id="runs"></div>

  </div></div></div>
</div>
<script>
const el = (t, cls, text) => {
  const e = document.createElement(t);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

async function refresh() {
  const runs = await (await fetch('/api/runs')).json();
  const box = document.getElementById('runs');
  box.innerHTML = '';
  for (const r of runs) {
    const card = el('div', 'card mb-2');
    const body = el('div', 'card-body d-flex flex-wrap align-items-center gap-3');
    const badge = r.status === 'running' ? el('span', 'badge bg-azure text-white', 'running')
      : r.status === 'error' ? el('span', 'badge bg-warning text-white', 'error')
      : r.equivalent ? el('span', 'badge bg-success text-white', 'EQUIVALENT')
      : el('span', 'badge bg-danger text-white', 'differences: ' + r.total_diffs);
    body.append(el('b', null, '#' + r.id), badge, el('code', null, r.config_path));
    const prog = Object.entries(r.progress || {})
      .map(([t, n]) => t + ': ' + n.toLocaleString('ru')).join(' · ');
    if (r.status === 'running' && prog) body.append(el('span', 'text-secondary', prog));
    if (r.error) body.append(el('span', 'text-danger', r.error));
    if (r.report_html) {
      const a = el('a', 'btn btn-sm', 'HTML report');
      a.href = '/runs/' + r.id + '/report.html'; a.target = '_blank';
      const j = el('a', 'btn btn-sm', 'JSON');
      j.href = '/runs/' + r.id + '/report.json'; j.target = '_blank';
      body.append(a, j);
    }
    card.append(body); box.append(card);
  }
}

document.getElementById('go').onclick = async () => {
  const err = document.getElementById('err');
  err.style.display = 'none';
  const resp = await fetch('/api/run', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({config_path: document.getElementById('cfg').value}),
  });
  if (!resp.ok) {
    err.textContent = (await resp.json()).error || 'error';
    err.style.display = 'block';
  }
  refresh();
};

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""
