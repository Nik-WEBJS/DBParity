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
           "diff": "[red]РАСХОЖДЕНИЯ[/red]",
           "error": "[yellow]ОШИБКА[/yellow]"}


def _print_summary(console: Console, run) -> None:
    rt = RichTable(title=f"DBParity v{__version__}: "
                         f"{run.source_label} → {run.target_label}")
    for col in ("Таблица", "Src", "Dst", "Совпало", "Разл.",
                "Нет в dst", "Лишние", "Дубли", "NULL PK", "Режим", "Статус"):
        rt.add_column(col, justify="right" if col not in ("Таблица", "Режим", "Статус") else "left")
    for t in run.tables:
        rt.add_row(t.table, str(t.src_rows), str(t.dst_rows), str(t.matched),
                   str(t.mismatched), str(t.missing_in_target),
                   str(t.extra_in_target), str(t.duplicate_pk), str(t.null_pk),
                   t.mode, _STATUS[t.status])
    console.print(rt)

    for t in run.tables:
        for w in t.warnings:
            console.print(f"[yellow]Предупреждение {t.table}:[/yellow] {w}")

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


def _cmd_validate(console: Console, path: str) -> int:
    """Команда validate: проверка конфига без подключения к БД.

    Коды выхода: 0 — конфиг валиден, 1 — найдены проблемы,
    2 — файл не найден или не разбирается как YAML.
    """
    p = Path(path)
    if not p.exists():
        console.print(f"[bold red]Ошибка:[/bold red] конфиг не найден: {p}")
        return 2
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        console.print("[bold red]Ошибка:[/bold red] не удалось разобрать YAML")
        console.print(str(e), style="red", markup=False)
        return 2

    problems = validate_config_dict(data)
    if problems:
        console.print(f"[bold red]Конфиг невалиден[/bold red] — "
                      f"проблем: {len(problems)}")
        for msg in problems:
            console.print(f"  ✗ {msg}", style="red", markup=False)
        return 1

    cfg = config_from_dict(data)
    console.print("[bold green]Конфиг валиден[/bold green]")
    src = cfg.source.label or cfg.source.type
    dst = cfg.target.label or cfg.target.type
    console.print(f"  Сверка: {src} → {dst}", markup=False)
    console.print(f"  Таблицы: "
                  f"{', '.join(cfg.tables) if cfg.tables else 'все общие'}",
                  markup=False)
    console.print(f"  Стратегия: {cfg.strategy}, workers: {cfg.workers}",
                  markup=False)
    reports = ", ".join(x for x in (cfg.report.html, cfg.report.json) if x)
    if reports:
        console.print(f"  Отчёты: {reports}", markup=False)
    return 0


def _cmd_history(console: Console, config_path: str, html) -> int:
    """Таймлайн дрейфа по истории инкрементальных прогонов."""
    from .core.incremental import (IncrementalState, default_state_path,
                                   state_fingerprint)
    from .report.render import write_timeline_html
    try:
        cfg = load_config(config_path)
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Ошибка:[/bold red] {e}")
        return 2
    if not cfg.incremental:
        console.print("[yellow]В конфиге нет карты incremental — "
                      "история дрейфа не ведётся.[/yellow]")
        return 2
    ifp = state_fingerprint(cfg)
    st = IncrementalState.load_or_create(default_state_path(ifp), ifp)
    hist = st.history
    if not hist:
        console.print("[yellow]История пуста: стейт-файл не найден или "
                      "с этим конфигом ещё не было прогонов.[/yellow]")
        return 2

    tables = sorted({n for h in hist for n in (h.get("tables") or {})})

    def entry_total(h: dict) -> int:
        return sum(int((v or {}).get("total_diffs", 0) or 0)
                   for v in (h.get("tables") or {}).values())

    rt = RichTable(title=f"Дрейф по прогонам (всего {len(hist)}, последние 15)")
    for col in ["Время", "Режим", *tables, "Σ дрейф"]:
        rt.add_column(col, justify="right" if col not in ("Время", "Режим") else "left")
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
            "[bold green]ДРЕЙФ НУЛЕВОЙ[/bold green] — по последнему прогону "
            "расхождений среди изменённых строк нет",
            border_style="green"))
    else:
        console.print(Panel(
            f"[bold red]Дрейф: {last_total}[/bold red] по последнему прогону",
            border_style="red"))
    if html:
        p = write_timeline_html(hist, cfg.source.label or cfg.source.type,
                                cfg.target.label or cfg.target.type, html)
        console.print(f"HTML-таймлайн: [bold]{p.resolve()}[/bold]")
    return 0


def _cmd_watch(console: Console, config_path: str, interval: float,
               stable: int, max_runs: int) -> int:
    """Режим наблюдения: инкрементальные прогоны до устойчиво нулевого дрейфа.

    Сценарий ночи переключения: dual-write включён, watch гоняет сверку
    каждые N секунд; когда дрейф нулевой `stable` раз подряд — зелёный
    сигнал и код выхода 0.
    """
    import time as _time
    from datetime import datetime as _dt

    try:
        cfg = load_config(config_path)
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Ошибка:[/bold red] {e}")
        return 2
    if not cfg.incremental:
        console.print("[yellow]В конфиге нет карты incremental — "
                      "режиму наблюдения нечего отслеживать.[/yellow]")
        return 2

    streak = 0
    for i in range(1, max_runs + 1):
        try:
            run = engine.run(cfg)
        except Exception as e:  # noqa: BLE001
            console.print(f"[bold red]Ошибка прогона {i}:[/bold red] {e}")
            return 2
        tracked = [t for t in run.tables if t.table in cfg.incremental]
        drift = sum(t.total_diffs for t in tracked)
        errors = [t.table for t in tracked if t.error]
        ts = _dt.now().strftime("%H:%M:%S")
        if errors:
            streak = 0
            console.print(f"[yellow]{ts} · прогон {i}: ошибки таблиц "
                          f"({', '.join(errors)}) — серия сброшена[/yellow]")
        elif drift == 0:
            streak += 1
            console.print(f"[green]{ts} · прогон {i}: дрейф 0 "
                          f"({streak}/{stable} подряд)[/green]")
        else:
            streak = 0
            per = ", ".join(f"{t.table}: {t.total_diffs}"
                            for t in tracked if t.total_diffs)
            console.print(f"[red]{ts} · прогон {i}: дрейф {drift} ({per})[/red]")
        if streak >= stable:
            console.print(Panel(
                f"[bold green]ДРЕЙФ НУЛЕВОЙ {stable} раз(а) подряд[/bold green] "
                f"— можно переключать трафик",
                border_style="green"))
            return 0
        if i < max_runs:
            _time.sleep(interval)
    console.print(Panel(
        f"[bold red]Лимит прогонов ({max_runs}) исчерпан[/bold red] — "
        f"дрейф так и не стабилизировался на нуле",
        border_style="red"))
    return 1


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
    pc.add_argument("--workers", type=int,
                    help="сверять N таблиц параллельно (соединение на поток)")
    pc.add_argument("--resume", action="store_true",
                    help="продолжить прерванную сверку с чекпоинта")
    pc.add_argument("--full", action="store_true",
                    help="игнорировать сохранённые инкрементальные "
                         "watermark'и — полная сверка (стейт обновится)")

    pd = sub.add_parser("demo", help="Демо на встроенных данных с расхождениями")
    pd.add_argument("--outdir", default="demo_out", help="каталог для демо-файлов")
    pd.add_argument("--workers", type=int, help="параллельные таблицы")

    pv = sub.add_parser("validate",
                        help="Проверка конфига без подключения к БД")
    pv.add_argument("-c", "--config", required=True, help="путь к config.yaml")

    ph = sub.add_parser("history",
                        help="Таймлайн дрейфа по истории инкрементальных прогонов")
    ph.add_argument("-c", "--config", required=True, help="путь к config.yaml")
    ph.add_argument("--html", help="сохранить HTML-таймлайн по указанному пути")

    ps = sub.add_parser("serve",
                        help="Локальная веб-консоль (браузер вместо терминала)")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8765)
    ps.add_argument("--workdir", default="dbparity_console",
                    help="каталог для отчётов консоли")
    ps.add_argument("--allow-remote", action="store_true",
                    help="разрешить бинд не на localhost (консоль БЕЗ "
                         "аутентификации — осознанный риск)")

    pw = sub.add_parser("watch",
                        help="Наблюдение: инкрементальные прогоны до "
                             "устойчиво нулевого дрейфа")
    pw.add_argument("-c", "--config", required=True, help="путь к config.yaml")
    pw.add_argument("--interval", type=float, default=300,
                    help="пауза между прогонами, сек (по умолчанию 300)")
    pw.add_argument("--stable", type=int, default=2,
                    help="сколько нулевых прогонов подряд считать успехом")
    pw.add_argument("--max-runs", type=int, default=100,
                    help="максимум прогонов до выхода с кодом 1")

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
            console.print(f"[bold red]Ошибка:[/bold red] {e}")
            return 2
        if args.allow_remote:
            console.print("[bold yellow]ВНИМАНИЕ:[/bold yellow] консоль "
                          "доступна из сети и не имеет аутентификации")
        console.print(f"Веб-консоль: [bold]http://{args.host}:{srv.port}/[/bold] "
                      f"(Ctrl+C — остановка)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            console.print("\n[dim]Остановлено[/dim]")
        return 0

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
        if args.workers:
            cfg.workers = max(1, args.workers)

        progress_ui = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TextColumn("{task.completed:>10.0f} строк"),
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
