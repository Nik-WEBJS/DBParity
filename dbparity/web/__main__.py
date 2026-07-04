"""Web console launcher: python -m dbparity.web [--port 8765]."""
import argparse

from .server import create_server


def main() -> int:
    p = argparse.ArgumentParser(prog="dbparity-web",
                                description="DBParity local web console")
    p.add_argument("--host", default="127.0.0.1",
                   help="interface (localhost only by default)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--workdir", default="dbparity_console",
                   help="directory for console reports")
    p.add_argument("--allow-remote", action="store_true",
                   help="allow binding to non-localhost (no authentication!)")
    args = p.parse_args()
    srv = create_server(args.host, args.port, args.workdir,
                        allow_remote=args.allow_remote)
    print(f"DBParity console: http://{args.host}:{srv.port}/  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
    return 0


raise SystemExit(main())
