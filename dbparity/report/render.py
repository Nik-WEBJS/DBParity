"""Рендеринг результатов: самодостаточный HTML (Tabler + Chart.js) и JSON."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import __version__
from ..core.models import RunResult

_TEMPLATES = Path(__file__).parent / "templates"


def _fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(("html", "htm", "j2")),
    )
    env.filters["num"] = _fmt_int
    return env


def _chart_data(run: RunResult) -> dict:
    t = run.totals
    return {
        "tables": [x.table for x in run.tables],
        "diffs": [x.total_diffs for x in run.tables],
        "donut": {
            "labels": ["Совпало", "Различия значений", "Нет в приёмнике",
                       "Лишние в приёмнике", "Дубли PK"],
            "data": [t["matched"], t["mismatched"], t["missing_in_target"],
                     t["extra_in_target"], t["duplicate_pk"]],
        },
    }


def render_html(run: RunResult) -> str:
    tpl = _env().get_template("report.html.j2")
    return tpl.render(
        run=run,
        totals=run.totals,
        chart=_chart_data(run),
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        version=__version__,
    )


def write_html(run: RunResult, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html(run), encoding="utf-8")
    return p


def write_json(run: RunResult, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(run.to_dict(), f, ensure_ascii=False, indent=2, default=str)
    return p
