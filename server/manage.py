#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from gizmoapp_server import create_app
from gizmoapp_server.config import load_settings
from gizmoapp_server.db import backup_database, database_readiness, database_summary, initialize_database
from gizmoapp_server.shells import available_shell_choices


def build_parser() -> argparse.ArgumentParser:
    shell_choices = available_shell_choices()
    parser = argparse.ArgumentParser(description="Manage the GizmoApp server.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-db", help="Create tables for the blank starter app.")
    init_parser.add_argument("--shell", choices=shell_choices)

    backup_parser = subparsers.add_parser("backup-db", help="Create a consistent SQLite backup.")
    backup_parser.add_argument("--output", type=Path)
    backup_parser.add_argument("--shell", choices=shell_choices)

    ready_parser = subparsers.add_parser("check-ready", help="Check database and schema readiness.")
    ready_parser.add_argument("--shell", choices=shell_choices)

    describe_parser = subparsers.add_parser("describe", help="Print a short runtime summary.")
    describe_parser.add_argument("--json", action="store_true", help="Reserved for future use.")
    describe_parser.add_argument("--shell", choices=shell_choices)

    run_parser = subparsers.add_parser("run-dev", help="Run the Flask development server.")
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", default=8001, type=int)
    run_parser.add_argument("--debug", action="store_true")
    run_parser.add_argument("--shell", choices=shell_choices)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run-dev" and os.environ.get("ALLOW_SERVER_RUN") != "1":
        parser.exit(
            2,
            "run-dev starts the local development server. "
            "Set ALLOW_SERVER_RUN=1 only when local serving is deliberate and explicitly approved.\n",
        )

    shell = getattr(args, "shell", None)

    if args.command == "init-db":
        config = load_settings(shell_variant=shell)
        initialize_database(config)
        print(f"Initialized database at {config['DB_PATH']}")
        return

    if args.command == "backup-db":
        config = load_settings(shell_variant=shell)
        output = args.output
        if output is None:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            output = config["REPO_ROOT"] / "var" / "backups" / f"gizmoapp-{stamp}.sqlite3"
        backup_database(config, output)
        print(f"Backed up database to {output}")
        return

    if args.command == "check-ready":
        config = load_settings(shell_variant=shell)
        ready, detail = database_readiness(config)
        print(detail)
        raise SystemExit(0 if ready else 1)

    app = create_app(shell_variant=shell)

    if args.command == "describe":
        summary = database_summary(app.config)
        print(f"App name: {app.config['APP_NAME']}")
        print(f"Shell: {app.config['APP_SHELL']}")
        print(f"URL prefix: {app.config['URL_PREFIX'] or '/'}")
        print(f"Database path: {app.config['DB_PATH']}")
        print(f"Sample nodes: {summary['sample_node_count']}")
        return

    if args.command == "run-dev":
        app.run(host=args.host, port=args.port, debug=args.debug)
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
