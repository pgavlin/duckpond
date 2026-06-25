"""Command-line entry point: `duckbill serve dashboard.py --db warehouse.duckdb`."""

import argparse
import os
from collections.abc import Sequence

from .server import serve


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="duckbill")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="serve a dashboard against a warehouse")
    s.add_argument("dashboard", help="path to a dashboard .py module")
    s.add_argument("--db", required=True,
                   help="warehouse DSN or path: a DuckDB/SQLite file, or "
                        "postgresql://, mysql://, snowflake:// (use ${VAR} for secrets)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8799)
    s.add_argument("--questions", default=None,
                   help="directory for saved Ask questions (default: questions/ next to the dashboard)")
    s.add_argument("--pool", type=int, default=4,
                   help="connection pool size for network backends (default 4)")

    b = sub.add_parser(
        "bundle",
        help="wrap a dashboard + its data into one self-contained `uv run` server "
             "script (data embedded as Parquet); the recipient just runs it")
    b.add_argument("dashboard", help="path to a dashboard .py module")
    b.add_argument("--db", required=True,
                   help="warehouse DSN or path: a DuckDB/SQLite file, or "
                        "postgresql://, mysql://, snowflake:// (use ${VAR} for secrets)")
    b.add_argument("-o", "--out", required=True,
                   help="output path for the server script (.py is appended if missing)")
    b.add_argument("--questions", default=None,
                   help="directory of saved Ask questions to embed (default: questions/ next to the dashboard)")
    b.add_argument("--static", action="store_true",
                   help="emit a static DuckDB-WASM site (a directory) instead of a uv-run server")

    d = sub.add_parser("docs", help="write a Markdown README of the warehouse")
    d.add_argument("dashboard", help="path to a dashboard .py module")
    d.add_argument("--db", required=True,
                   help="warehouse DSN or path: a DuckDB/SQLite file, or "
                        "postgresql://, mysql://, snowflake:// (use ${VAR} for secrets)")
    d.add_argument("-o", "--out", default=None, help="output path (default: stdout)")

    args = ap.parse_args(argv)
    if args.cmd == "serve":
        serve(args.dashboard, args.db, host=args.host, port=args.port,
              questions_dir=args.questions, pool=args.pool)
    elif args.cmd == "bundle":
        if args.static:
            from .static_bundle import build_static
            build_static(args.dashboard, args.db, args.out, questions_dir=args.questions)
        else:
            from . import bundle as _bundle

            base, ext = os.path.splitext(args.out)
            out = args.out if ext == ".py" else base + ".py"
            _bundle.build_server(args.dashboard, args.db, out, questions_dir=args.questions)
    elif args.cmd == "docs":
        from .backends import open_backend
        from .docs import to_markdown
        from .loader import load_dashboard
        dash = load_dashboard(args.dashboard)
        md = to_markdown(dash.title, dash.readme, open_backend(args.db).docs())
        if args.out:
            with open(args.out, "w") as f:
                f.write(md)
            print(f"wrote {args.out}")
        else:
            print(md, end="")


if __name__ == "__main__":
    main()
