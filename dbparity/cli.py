"""CLI: dbparity compare | demo."""
from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table as RichTable

from . import __version__
from .config import load_config
from .core import engine
from .report.render import write_html, write_json

_STATUS = {"ok": "[green]OK[/green]",
           "diff": "[red]РАСХОЖДЕНИЯ[/red]",
           "error": "[yellow]ОШИБКА[/yellow]"}


def _print_summary(console: Console, run) -> None:
    rt = RichTable(title=f"DBParity v{__version__}: "
                         f"{run.source_label} → {run.target_label}")
    for col in ("Таблица", "Src", "Dst", "Совпало", "Разл.",
                "Нет в dst", "Лишние", "Дубли", "Статус"):
        rt.add_column(col, justify="right" if col not in ("Таблица", "Статус") else "left")
    for t in run.tables:
        rt.add_row(t.table, str(t.src_rows), str(t.dst_rows), str(t.matched),
                   str(t.mismatched), str(t.missing_in_target),
                   str(t.extra_in_target), str(t.duplicate_pk),
                   _STATUS[t.status])
    console.print(rt)

    if run.tables_only_in_source:
        console.print(f"[orange3]Таблицы только в источнике:[/orange3] "
                      f"{', '.join(run.tables_only_in_source)}")
    if run.tables_only_in_target:
        console.print(f"[yellow]Таблицы только в приёмнике:[/yellow] "
                      f"{', '.join(run.tables_only_in_target)}")
    for d in run.schema_diffs:
        parts = []
        if d.missing_in_target:
            parts.append(f"нет колонок в приёмнике: {', '.join(d.missing_in_target)}")
        if d.extra_in_target:
            parts.append(f"лишние колонки: {', '.join(d.extra_in_target)}")
        if d.type_changes:
            parts.append(f"смена типов: {len(d.type_changes)}")
        if d.pk_mismatch:
            parts.append("PK различается")
        console.print(f"[orange3]Схема {d.table}:[/orange3] {'; '.join(parts)}")

    t = run.totals
    if run.equivalent:
        console.print(Panel(
            f"[bold green]ЭКВИВАЛЕНТНО[/bold green] — "
            f"{t['matched']:,} строк совпало, расхождений нет".replace(",", " "),
            border_style="green"))
    else:
        console.print(Panel(
            f"[bold red]НЕ ЭКВИВАЛЕНТНО[/bold red] — расхождений: "
            f"{t['total_diffs']:,} (совпадение {t['match_pct']}%)".replace(",", " "),
            border_style="red"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="dbparity",
        description="Верификация эквивалентности данных при миграциях БД",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("compare", help="Сверка по YAML-конфигу")
    pc.add_argument("-c", "--config", required=True, help="путь к config.yaml")
    pc.add_argument("--html", help="переопределить путь HTML-отчёта")
    pc.add_argument("--json", help="переопределить путь JSON-отчёта")

    pd = sub.add_parser("demo", help="Демо на встроенных данных с расхождениями")
    pd.add_argument("--outdir", default="demo_out", help="каталог для демо-файлов")

    args = parser.parse_args(argv)
    console = Console()

    try:
        if args.cmd == "demo":
            from .demo.seed import build_demo
            cfg = build_demo(args.outdir)
            console.print(f"[dim]Демо-БД и конфиг созданы в: {Path(args.outdir).resolve()}[/dim]")
        else:
            cfg = load_config(args.config)
            if args.html:
                cfg.report.html = args.html
            if args.json:
                cfg.report.json = args.json
        run = engine.run(cfg)
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Ошибка:[/bold red] {e}")
        return 2

    _print_summary(console, run)
    if cfg.report.html:
        p = write_html(run, cfg.report.html)
        console.print(f"HTML-отчёт: [bold]{p.resolve()}[/bold]")
    if cfg.report.json:
        p = write_json(run, cfg.report.json)
        console.print(f"JSON-отчёт: [bold]{p.resolve()}[/bold]")
    if args.cmd == "demo":
        console.print("[dim]Демо намеренно содержит расхождения — "
                      "код выхода 1 здесь ожидаем.[/dim]")
    return 0 if run.equivalent else 1
