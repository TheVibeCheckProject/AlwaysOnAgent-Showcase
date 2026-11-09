#!/usr/bin/env python3
"""Run the daemon: python -m core.daemon [--interval N] [--once] ...

Moved here from the old core/daemon.py's __main__ block on 2026-11-04 (a package
__init__ never runs as __main__). Behaviour identical; only the workspace
path changes: __file__ is now core/daemon/__main__.py, so the repo root is
parent.parent.parent instead of parent.parent.
"""
from pathlib import Path

from . import AutonomousDaemon, parse_args


def main() -> None:
    args = parse_args()
    workspace = Path(__file__).resolve().parent.parent.parent

    daemon = AutonomousDaemon(
        workspace_dir=workspace,
        interval_seconds=args.interval,
        db_file=args.db_file,
        log_file=args.log_file,
        agy_path=args.agy_path
    )
    daemon.run(run_once=args.once)


if __name__ == "__main__":
    main()
