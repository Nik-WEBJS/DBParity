"""CLI: dbparity compare | demo | validate."""
from __future__ import annotations

import argparse
import threading
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table as RichTable

from . import __version__
from .config import config_from_dict, load_config, validate_config_dict
from .core import engine
from .report.render import write_html, write_json

_STATUS = {"ok": "[green]OK[/green]",
           "diff": "[red]DIFF[/red]",
           "error": "[yellow]ERROR[/yellow]"}


def _print_summary(console: Console, run) -> None:
    rt = RichTable(title=f"DBParity v{__version__}: "
                         f"{run.source_label} → {run.target_label}")
    for col in ("Table", "Src", "Dst", "Matched", "Diff",
                "Missing", "Extra", "Dups", "NULL PK", "Mode", "Status"):
        rt.add_column(col, justify="right" if col not in ("Table", "Mode", "Status") else "left")
    for t in run.tables:
        rt.add_row(t.table, str(t.src_rows), str(t.dst_rows), str(t.matched),
                   str(t.mismatched), str(t.missing_in_target),
                   str(t.extra_in_target), str(t.duplicate_pk), str(t.null_pk),
                   t.mode, _STATUS[t.status])
    console.print(rt)

    for t in run.tables:
        for w in t.warnings:
            console.print(f"[yellow]Warning {t.table}:[/yellow] {w}")

    if run.tables_only_in_source:
        console.print(f"[orange3]Tables only in source:[/orange3] "
                      f"{', '.join(run.tables_only_in_source)}")
    if run.tables_only_in_target:
        console.print(f"[yellow]Tables only in target:[/yellow] "
                      f"{', '.join(run.tables_only_in_target)}")
    for d in run.schema_diffs:
        parts = []
        if d.missing_in_target:
            parts.append(f"columns missing in target: {', '.join(d.missing_in_target)}")
        if d.extra_in_target:
            parts.append(f"extra columns in target: {', '.join(d.extra_in_target)}")
        if d.type_changes:
            parts.append(f"type changes: {len(d.type_changes)}")
        if d.pk_mismatch:
            parts.append("PK differs")
        console.print(f"[orange3]Schema {d.table}:[/orange3] {'; '.join(parts)}")

    t = run.totals
    if run.equivalent:
        matched = f"{t['matched']:,}".replace(",", " ")
        console.print(Panel(
            f"[bold green]EQUIVALENT[/bold green] — "
            f"{matched} rows matched, no differences",
            border_style="green"))
    else:
        diffs = f"{t['total_diffs']:,}".replace(",", " ")
        console.print(Panel(
            f"[bold red]NOT EQUIVALENT[/bold red] — differences: "
            f"{diffs} (match {t['match_pct']}%)",
            border_style="red"))


def _cmd_validate(console: Console, path: str) -> int:
    """The validate command: check the config without connecting to databases.

    Exit codes: 0 — the config is valid, 1 — problems found,
    2 — the file is missing or cannot be parsed as YAML.
    """
    p = Path(path)
    if not p.exists():
        console.print(f"[bold red]Error:[/bold red] Config not found: {p}")
        return 2
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        console.print("[bold red]Error:[/bold red] failed to parse YAML")
        console.print(str(e), style="red", markup=False)
        return 2

    problems = validate_config_dict(data)
    if problems:
        console.print(f"[bold red]Config is invalid[/bold red] — "
                      f"{len(problems)} problem(s)")
        for msg in problems:
            console.print(f"  ✗ {msg}", style="red", markup=False)
        return 1

    cfg = config_from_dict(data)
    console.print("[bold green]Config is valid[/bold green]")
    src = cfg.source.label or cfg.source.type
    dst = cfg.target.label or cfg.target.type
    console.print(f"  Comparison: {src} → {dst}", markup=False)
    console.print(f"  Tables: "
                  f"{', '.join(cfg.tables) if cfg.tables else 'all common'}",
                  markup=False)
    console.print(f"  Strategy: {cfg.strategy}, workers: {cfg.workers}",
                  markup=False)
    reports = ", ".join(x for x in (cfg.report.html, cfg.report.json) if x)
    if reports:
        console.print(f"  Reports: {reports}", markup=False)
    return 0


def _cmd_history(console: Console, config_path: str, html) -> int:
    """Drift timeline from the history of incremental runs."""
    from .core.incremental import (IncrementalState, default_state_path,
                                   state_fingerprint)
    from .report.render import write_timeline_html
    try:
        cfg = load_config(config_path)
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Error:[/bold red] {e}")
        return 2
    if not cfg.incremental:
        console.print("[yellow]The config has no incremental map — "
                      "drift history is not tracked.[/yellow]")
        return 2
    ifp = state_fingerprint(cfg)
    st = IncrementalState.load_or_create(default_state_path(ifp), ifp)
    hist = st.history
    if not hist:
        console.print("[yellow]Run history is empty: the state file was not "
                      "found or no runs have been made with this config "
                      "yet.[/yellow]")
        return 2

    tables = sorted({n for h in hist for n in (h.get("tables") or {})})

    def entry_total(h: dict) -> int:
        return sum(int((v or {}).get("total_diffs", 0) or 0)
                   for v in (h.get("tables") or {}).values())

    rt = RichTable(title=f"Drift by run (total {len(hist)}, last 15)")
    for col in ["Time", "Mode", *tables, "Σ drift"]:
        rt.add_column(col, justify="right" if col not in ("Time", "Mode") else "left")
    for h in hist[-15:]:
        total = entry_total(h)
        style = "green" if total == 0 else "red"
        rt.add_row(
            str(h.get("ts", ""))[:19],
            "full" if h.get("full") else "incr",
            *[str(((h.get("tables") or {}).get(n) or {}).get("total_diffs", "—"))
              for n in tables],
            f"[{style}]{total}[/{style}]",
        )
    console.print(rt)

    last_total = entry_total(hist[-1])
    if last_total == 0:
        console.print(Panel(
            "[bold green]ZERO DRIFT[/bold green] — no differences among "
            "changed rows in the latest run",
            border_style="green"))
    else:
        console.print(Panel(
            f"[bold red]Drift: {last_total}[/bold red] in the latest run",
            border_style="red"))
    if html:
        p = write_timeline_html(hist, cfg.source.label or cfg.source.type,
                                cfg.target.label or cfg.target.type, html)
        console.print(f"HTML timeline: [bold]{p.resolve()}[/bold]")
    return 0


def _cmd_watch(console: Console, config_path: str, interval: float,
               stable: int, max_runs: int) -> int:
    """Watch mode: incremental runs until drift is stably zero.

    The cutover-night scenario: dual-write is on, watch reruns the
    comparison every N seconds; once drift is zero `stable` times in a
    row — green light and exit code 0.
    """
    import time as _time
    from datetime import datetime as _dt

    try:
        cfg = load_config(config_path)
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Error:[/bold red] {e}")
        return 2
    if not cfg.incremental:
        console.print("[yellow]The config has no incremental map — "
                      "watch mode has nothing to track.[/yellow]")
        return 2

    streak = 0
    for i in range(1, max_runs + 1):
        try:
            run = engine.run(cfg)
        except Exception as e:  # noqa: BLE001
            console.print(f"[bold red]Run {i} error:[/bold red] {e}")
            return 2
        tracked = [t for t in run.tables if t.table in cfg.incremental]
        drift = sum(t.total_diffs for t in tracked)
        errors = [t.table for t in tracked if t.error]
        ts = _dt.now().strftime("%H:%M:%S")
        if errors:
            streak = 0
            console.print(f"[yellow]{ts} · run {i}: table errors "
                          f"({', '.join(errors)}) — streak reset[/yellow]")
        elif drift == 0:
            streak += 1
            console.print(f"[green]{ts} · run {i}: drift 0 "
                          f"({streak}/{stable} in a row)[/green]")
        else:
            streak = 0
            per = ", ".join(f"{t.table}: {t.total_diffs}"
                            for t in tracked if t.total_diffs)
            console.print(f"[red]{ts} · run {i}: drift {drift} ({per})[/red]")
        if streak >= stable:
            console.print(Panel(
                f"[bold green]ZERO DRIFT {stable} time(s) in a row[/bold green] "
                f"— safe to cut over",
                border_style="green"))
            return 0
        if i < max_runs:
            _time.sleep(interval)
    console.print(Panel(
        f"[bold red]Run limit ({max_runs}) exhausted[/bold red] — "
        f"drift never stabilized at zero",
        border_style="red"))
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="dbparity",
        description="Data equivalence verification for database migrations",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("compare", help="Compare using a YAML config")
    pc.add_argument("-c", "--config", required=True, help="path to config.yaml")
    pc.add_argument("--html", help="override the HTML report path")
    pc.add_argument("--json", help="override the JSON report path")
    pc.add_argument("--workers", type=int,
                    help="compare N tables in parallel (one connection per thread)")
    pc.add_argument("--resume", action="store_true",
                    help="resume an interrupted comparison from a checkpoint")
    pc.add_argument("--full", action="store_true",
                    help="ignore saved incremental watermarks — "
                         "full comparison (the state will be updated)")

    pd = sub.add_parser("demo", help="Demo on built-in data with differences")
    pd.add_argument("--outdir", default="demo_out", help="directory for demo files")
    pd.add_argument("--workers", type=int, help="parallel tables")

    pv = sub.add_parser("validate",
                        help="Validate the config without connecting to databases")
    pv.add_argument("-c", "--config", required=True, help="path to config.yaml")

    ph = sub.add_parser("history",
                        help="Drift timeline from the history of incremental runs")
    ph.add_argument("-c", "--config", required=True, help="path to config.yaml")
    ph.add_argument("--html", help="save the HTML timeline to the given path")

    ps = sub.add_parser("serve",
                        help="Local web console (a browser instead of the terminal)")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8765)
    ps.add_argument("--workdir", default="dbparity_console",
                    help="directory for console reports")
    ps.add_argument("--allow-remote", action="store_true",
                    help="allow binding to non-localhost (the console has NO "
                         "authentication — a deliberate risk)")

    pw = sub.add_parser("watch",
                        help="Watch mode: incremental runs until drift "
                             "is stably zero")
    pw.add_argument("-c", "--config", required=True, help="path to config.yaml")
    pw.add_argument("--interval", type=float, default=300,
                    help="pause between runs, seconds (default 300)")
    pw.add_argument("--stable", type=int, default=2,
                    help="how many consecutive zero-drift runs count as success")
    pw.add_argument("--max-runs", type=int, default=100,
                    help="maximum number of runs before exiting with code 1")

    args = parser.parse_args(argv)
    console = Console()

    if args.cmd == "validate":
        return _cmd_validate(console, args.config)
    if args.cmd == "history":
        return _cmd_history(console, args.config, args.html)
    if args.cmd == "watch":
        return _cmd_watch(console, args.config, args.interval,
                          max(1, args.stable), max(1, args.max_runs))
    if args.cmd == "serve":
        from .web import create_server
        try:
            srv = create_server(args.host, args.port, args.workdir,
                                allow_remote=args.allow_remote)
        except ValueError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            return 2
        if args.allow_remote:
            console.print("[bold yellow]WARNING:[/bold yellow] the console "
                          "is network-accessible and has no authentication")
        console.print(f"Web console: [bold]http://{args.host}:{srv.port}/[/bold] "
                      f"(Ctrl+C to stop)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped[/dim]")
        return 0

    try:
        if args.cmd == "demo":
            from .demo.seed import build_demo
            cfg = build_demo(args.outdir)
            console.print(f"[dim]Demo databases and config created in: {Path(args.outdir).resolve()}[/dim]")
        else:
            cfg = load_config(args.config)
            if args.html:
                cfg.report.html = args.html
            if args.json:
                cfg.report.json = args.json
        if args.workers:
            cfg.workers = max(1, args.workers)

        progress_ui = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TextColumn("{task.completed:>10.0f} rows"),
            console=console, transient=True,
        )
        task_ids = {}
        lock = threading.Lock()

        def on_progress(table: str, n: int) -> None:
            with lock:
                if table not in task_ids:
                    task_ids[table] = progress_ui.add_task(table, total=None)
                progress_ui.update(task_ids[table], completed=n)

        with progress_ui:
            run = engine.run(cfg, on_progress=on_progress,
                             resume=getattr(args, "resume", False),
                             full=getattr(args, "full", False))
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Error:[/bold red] {e}")
        return 2

    _print_summary(console, run)
    if cfg.report.html:
        p = write_html(run, cfg.report.html)
        console.print(f"HTML report: [bold]{p.resolve()}[/bold]")
    if cfg.report.json:
        p = write_json(run, cfg.report.json)
        console.print(f"JSON report: [bold]{p.resolve()}[/bold]")
    if args.cmd == "demo":
        console.print("[dim]The demo intentionally contains differences — "
                      "exit code 1 is expected.[/dim]")
    return 0 if run.equivalent else 1
