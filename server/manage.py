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

    credits = subparsers.add_parser("credit-requests", help="List pending reply allowance requests (no email addresses).")
    credits.add_argument("--shell", choices=shell_choices)
    review = subparsers.add_parser("review-credit-request", help="Approve or decline one allowance request, once.")
    review.add_argument("request_id")
    review.add_argument("--replies", type=int, default=50)
    review.add_argument("--decline", action="store_true")
    review.add_argument("--shell", choices=shell_choices)
    admin = subparsers.add_parser("grant-admin", help="Allow one existing verified account to review credit requests.")
    admin.add_argument("--email", required=True)
    admin.add_argument("--shell", choices=shell_choices)

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

    if args.command == "grant-admin":
        from gizmoapp_server.db import get_db, WRITE_LOCK
        from gizmoapp_server.account_api import CREDIT_REVIEWER_EMAIL
        if args.email.strip().casefold() != CREDIT_REVIEWER_EMAIL:
            parser.error("Credit review is restricted to the designated operator account.")
        with app.app_context(), WRITE_LOCK:
            connection = get_db()
            user = connection.execute("SELECT id FROM account_users WHERE email=? COLLATE NOCASE", (args.email,)).fetchone()
            if not user:
                parser.error("No verified account with that email.")
            connection.execute("DELETE FROM account_admins WHERE user_id<>?", (user["id"],))
            connection.execute("INSERT OR IGNORE INTO account_admins VALUES (?)", (user["id"],))
            connection.commit()
            print("Credit review enabled for the account.")
        return

    if args.command == "credit-requests":
        from gizmoapp_server.db import get_db
        with app.app_context():
            rows = get_db().execute("SELECT id,created_at FROM account_credit_requests WHERE state='pending' ORDER BY created_at").fetchall()
            for row in rows:
                print(f"{row['id']}  {datetime.fromtimestamp(row['created_at'], UTC).isoformat()}")
            print(f"{len(rows)} pending")
        return

    if args.command == "review-credit-request":
        from gizmoapp_server.account_usage import review_credit_request
        with app.app_context():
            try:
                changed = review_credit_request(args.request_id, args.replies, decline=args.decline)
            except ValueError as error:
                parser.error(str(error))
            print("Request reviewed." if changed else "No pending request with that ID; no balance changed.")
        return

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
