"""Запуск веб-консоли: python -m dbparity.web [--port 8765]."""
import argparse

from .server import create_server


def main() -> int:
    p = argparse.ArgumentParser(prog="dbparity-web",
                                description="Локальная веб-консоль DBParity")
    p.add_argument("--host", default="127.0.0.1",
                   help="интерфейс (по умолчанию только localhost)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--workdir", default="dbparity_console",
                   help="каталог для отчётов консоли")
    p.add_argument("--allow-remote", action="store_true",
                   help="разрешить бинд не на localhost (без аутентификации!)")
    args = p.parse_args()
    srv = create_server(args.host, args.port, args.workdir,
                        allow_remote=args.allow_remote)
    print(f"DBParity консоль: http://{args.host}:{srv.port}/  (Ctrl+C — стоп)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    return 0


raise SystemExit(main())
