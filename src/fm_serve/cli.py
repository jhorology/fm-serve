"""CLI for fm-serve."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fm-serve",
        description="Always-on Apple Foundation Models API (fm serve + keepalive proxy)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("FM_SERVE_HOST", "127.0.0.1"),
        help="Public bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FM_SERVE_PORT", "1976")),
        help="Public bind port (default: 1976)",
    )
    parser.add_argument(
        "--backend-port",
        type=int,
        default=None,
        help="Internal fm serve port (default: ephemeral localhost port)",
    )
    parser.add_argument(
        "--fm-bin",
        default=os.environ.get("FM_BIN", "/usr/bin/fm"),
        help="Path to fm binary (default: /usr/bin/fm)",
    )
    parser.add_argument(
        "--keepalive-timeout",
        type=float,
        default=float(os.environ.get("FM_SERVE_KEEPALIVE_TIMEOUT", "3600")),
        help="Idle seconds before releasing sleep prevention (default: 3600)",
    )
    parser.add_argument(
        "--no-keepalive",
        action="store_true",
        help="Disable macOS sleep prevention",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify fm availability and exit",
    )

    args = parser.parse_args(argv)

    if args.check:
        fm = Path(args.fm_bin)
        if not fm.exists() and not shutil.which(args.fm_bin):
            print(f"fm not found: {args.fm_bin}")
            return 1
        print(f"fm ok: {args.fm_bin}")
        return 0

    from fm_serve.proxy import run_server

    run_server(
        host=args.host,
        port=args.port,
        backend_port=args.backend_port,
        fm_bin=args.fm_bin,
        keepalive_timeout=args.keepalive_timeout,
        enable_keepalive=not args.no_keepalive,
    )
    return 0
