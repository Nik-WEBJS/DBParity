"""Result rendering: self-contained HTML (Tabler + Chart.js) and JSON."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import __version__
from ..core.models import REPORT_SCHEMA_VERSION, RunResult

_TEMPLATES = Path(__file__).parent / "templates"


def _fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _fmt_ts(ts) -> str:
    """A run-time ISO string → a readable form (for the timeline)."""
    try:
        return (datetime.fromisoformat(str(ts))
                .strftime("%Y-%m-%d %H:%M:%S UTC"))
    except ValueError:
        return str(ts)      # not ISO — show as is


def _fmt_ts_short(ts) -> str:
    """A time ISO string → a short X-axis label for the line chart."""
    try:
        return datetime.fromisoformat(str(ts)).strftime("%d.%m %H:%M")
    except ValueError:
        return str(ts)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(("html", "htm", "j2")),
    )
    env.filters["num"] = _fmt_int
    env.filters["ts"] = _fmt_ts
    return env


def _chart_data(run: RunResult) -> dict:
    t = run.totals
    return {
        "tables": [x.table for x in run.tables],
        "diffs": [x.total_diffs for x in run.tables],
        "donut": {
            "labels": ["Matched", "Value mismatches", "Missing in target",
                       "Extra in target", "Duplicate PKs"],
            "data": [t["matched"], t["mismatched"], t["missing_in_target"],
                     t["extra_in_target"], t["duplicate_pk"]],
        },
    }


def render_html(run: RunResult) -> str:
    tpl = _env().get_template("report.html.j2")
    # The report schema version is appended to the generated string: both
    # variables are used only in the template footer, so the footer gets
    # "· report schema vN" without touching the template itself.
    generated = (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                 + f" · report schema v{REPORT_SCHEMA_VERSION}")
    return tpl.render(
        run=run,
        totals=run.totals,
        chart=_chart_data(run),
        generated=generated,
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


# ---------------------------------------------------------------------------
# Drift timeline (v0.5): HTML from the history of incremental runs.
# The history is a list of IncrementalState.record_run entries
# (see core/incremental).
# ---------------------------------------------------------------------------

def _entry_total(entry: dict) -> int:
    """The total drift of a history entry (sum of total_diffs across tables)."""
    return sum(int((v or {}).get("total_diffs", 0) or 0)
               for v in (entry.get("tables") or {}).values())


def _timeline_chart(history: list) -> dict:
    """Line-chart data: the X axis is time, one series per table + the total.

    A table absent from some run (the incremental map changed) gets
    None — Chart.js draws a gap (spanGaps connects the points).
    """
    tables = sorted({name for h in history for name in (h.get("tables") or {})})
    return {
        "labels": [_fmt_ts_short(h.get("ts", "")) for h in history],
        "total": [_entry_total(h) for h in history],
        "series": [{"label": name,
                    "data": [((h.get("tables") or {}).get(name) or {})
                             .get("total_diffs") for h in history]}
                   for name in tables],
    }


def render_timeline_html(history: list, source_label: str,
                         target_label: str) -> str:
    """An HTML drift timeline for a series of incremental runs.

    Scenario: during dual-write the comparison runs on a schedule, the
    integrator watches the total_diffs trend "toward zero" and decides
    whether it is time to cut over. The page is self-contained, in the
    style of the main report (Tabler + Chart.js): the verdict for the
    latest run, KPIs, a line chart, the last 20 runs.
    """
    hist = [h for h in (history or []) if isinstance(h, dict)]
    last = hist[-1] if hist else None
    last_total = _entry_total(last) if last else 0
    prev_total = _entry_total(hist[-2]) if len(hist) >= 2 else None
    if prev_total is None:
        trend = {"arrow": "—", "cls": "secondary", "note": "single run"}
    elif last_total < prev_total:
        trend = {"arrow": "↓", "cls": "success",
                 "note": f"{prev_total} → {last_total}"}
    elif last_total > prev_total:
        trend = {"arrow": "↑", "cls": "danger",
                 "note": f"{prev_total} → {last_total}"}
    else:
        trend = {"arrow": "→", "cls": "secondary", "note": "no change"}
    tpl = _env().get_template("timeline.html.j2")
    return tpl.render(
        history=hist,
        recent=[(h, _entry_total(h)) for h in reversed(hist[-20:])],
        chart=_timeline_chart(hist),
        last=last,
        last_total=last_total,
        trend=trend,
        runs_total=len(hist),
        runs_full=sum(1 for h in hist if h.get("full")),
        source_label=source_label,
        target_label=target_label,
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        version=__version__,
    )


def write_timeline_html(history: list, source_label: str, target_label: str,
                        path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_timeline_html(history, source_label, target_label),
                 encoding="utf-8")
    return p
