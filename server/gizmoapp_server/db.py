from __future__ import annotations

import sqlite3
import threading
import time
import os
import tempfile
from pathlib import Path
from typing import Any

from flask import current_app, g

SCHEMA_MIGRATIONS = {
    1: """
CREATE TABLE IF NOT EXISTS sample_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    accent_color TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    radius REAL NOT NULL DEFAULT 0.11,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
""",
    2: """
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
""",
    3: """
CREATE TABLE IF NOT EXISTS debate_discussions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'live',
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS debate_versions (
    discussion_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    question_json TEXT NOT NULL,
    perspectives_json TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    framing_json TEXT,
    contributions_json TEXT NOT NULL DEFAULT '[]',
    synthesis_json TEXT,
    followups_json TEXT NOT NULL DEFAULT '[]',
    run_json TEXT NOT NULL,
    reflection TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (discussion_id, version),
    FOREIGN KEY (discussion_id) REFERENCES debate_discussions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS debate_discussions_updated
    ON debate_discussions(updated_at DESC);
""",
    4: """
CREATE TABLE IF NOT EXISTS debate_sessions (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    participants_json TEXT NOT NULL,
    messages_json TEXT NOT NULL DEFAULT '[]',
    draft TEXT NOT NULL DEFAULT '',
    debate_started INTEGER NOT NULL DEFAULT 0 CHECK (debate_started IN (0, 1)),
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS debate_sessions_updated
    ON debate_sessions(updated_at DESC);
""",
    5: """
ALTER TABLE debate_sessions
    ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0);
""",
    6: """
ALTER TABLE debate_sessions
    ADD COLUMN pending_user_messages_json TEXT NOT NULL DEFAULT '[]';
""",
}
LATEST_SCHEMA_VERSION = max(SCHEMA_MIGRATIONS)
BUSY_TIMEOUT_MS = 10_000
WRITE_RETRY_DELAYS = (0.05, 0.15, 0.35)
WRITE_LOCK = threading.RLock()


def _connect(db_path: str, config: dict[str, Any] | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if config is not None:
        requested_journal = str(config.get("SQLITE_JOURNAL_MODE", "DELETE")).upper()
        effective_journal = str(
            connection.execute(f"PRAGMA journal_mode = {requested_journal}").fetchone()[0]
        ).upper()
        if effective_journal != requested_journal:
            connection.close()
            raise RuntimeError(
                f"SQLite did not enable requested journal mode {requested_journal}; got {effective_journal}."
            )
        requested_synchronous = str(config.get("SQLITE_SYNCHRONOUS", "FULL")).upper()
        connection.execute(f"PRAGMA synchronous = {requested_synchronous}")
        effective_synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        expected_synchronous = {"NORMAL": 1, "FULL": 2, "EXTRA": 3}[requested_synchronous]
        if effective_synchronous != expected_synchronous:
            connection.close()
            raise RuntimeError(
                f"SQLite did not enable requested synchronous mode {requested_synchronous}."
            )
    return connection


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _applied_migrations(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row["version"]) for row in rows}


def _apply_migrations(connection: sqlite3.Connection) -> None:
    _ensure_migration_table(connection)
    applied = _applied_migrations(connection)

    for version, sql in sorted(SCHEMA_MIGRATIONS.items()):
        if version in applied:
            continue
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{sql}\n"
                f"INSERT INTO schema_migrations (version) VALUES ({int(version)});\n"
                "COMMIT;"
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = _connect(str(current_app.config["DB_PATH"]), current_app.config)
    return g.db


def close_db(_: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def initialize_database(config: dict) -> None:
    connection = _connect(str(config["DB_PATH"]), config)
    try:
        with WRITE_LOCK:
            _apply_migrations(connection)
            connection.commit()
    finally:
        connection.close()


def schema_version(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0)


def verify_database_schema(config: dict) -> None:
    connection = _connect(str(config["DB_PATH"]), config)
    try:
        current_version = schema_version(connection)
    finally:
        connection.close()
    if current_version != LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema is version {current_version}; expected {LATEST_SCHEMA_VERSION}. "
            "Run: python server/manage.py init-db"
        )


def database_readiness(config: dict) -> tuple[bool, dict[str, Any]]:
    try:
        connection = _connect(str(config["DB_PATH"]), config)
        try:
            connection.execute("SELECT 1").fetchone()
            current_version = schema_version(connection)
            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).upper()
            synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
            connection.execute("PRAGMA busy_timeout = 1000")
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return False, {"database": "unavailable", "detail": str(exc)}

    ready = current_version == LATEST_SCHEMA_VERSION
    return ready, {
        "database": "ready" if ready else "schema-outdated",
        "schemaVersion": current_version,
        "expectedSchemaVersion": LATEST_SCHEMA_VERSION,
        "sqliteJournalMode": journal_mode,
        "sqliteSynchronous": synchronous,
    }


def backup_database(config: dict, output_path: Path) -> Path:
    source_path = Path(config["DB_PATH"])
    if not source_path.exists():
        raise FileNotFoundError(f"Database does not exist: {source_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}-", suffix=".tmp", dir=output_path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = _connect(str(source_path), config)
        destination = sqlite3.connect(temporary)
        source.backup(destination)
        destination.commit()
        findings = [str(row[0]) for row in destination.execute("PRAGMA quick_check").fetchall()]
        if findings != ["ok"]:
            raise RuntimeError(f"SQLite backup integrity check failed: {'; '.join(findings[:5])}")
        destination.close()
        destination = None
        source.close()
        source = None
        temporary.chmod(0o600)
        file_descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(file_descriptor)
        finally:
            os.close(file_descriptor)
        os.replace(temporary, output_path)
        directory_descriptor = os.open(output_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        temporary.unlink(missing_ok=True)
    return output_path


def fetch_sample_nodes(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, slug, label, description, accent_color, x, y, radius, created_at
        FROM sample_nodes
        ORDER BY id
        LIMIT 500
        """
    ).fetchall()
    return [dict(row) for row in rows]


def search_sample_nodes(connection: sqlite3.Connection, query: str, limit: int = 8) -> list[dict[str, Any]]:
    term = query.strip().lower()
    if not term:
        return []

    rows = connection.execute(
        """
        SELECT id, slug, label, description, accent_color, x, y, radius, created_at
        FROM sample_nodes
        WHERE lower(slug) LIKE ?
           OR lower(label) LIKE ?
           OR lower(description) LIKE ?
        ORDER BY id
        LIMIT ?
        """,
        (f"%{term}%", f"%{term}%", f"%{term}%", limit),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_sample_node(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    parameters = (
        payload["slug"],
        payload["label"],
        payload["description"],
        payload["accent_color"],
        payload["x"],
        payload["y"],
        payload["radius"],
    )
    with WRITE_LOCK:
        for delay in (*WRITE_RETRY_DELAYS, None):
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO sample_nodes (slug, label, description, accent_color, x, y, radius)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    parameters,
                )
                connection.commit()
                break
            except sqlite3.OperationalError as exc:
                connection.rollback()
                if "locked" not in str(exc).lower() or delay is None:
                    raise
                time.sleep(delay)

    row = connection.execute(
        """
        SELECT id, slug, label, description, accent_color, x, y, radius, created_at
        FROM sample_nodes
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row)


def database_summary(config: dict) -> dict[str, Any]:
    connection = _connect(str(config["DB_PATH"]), config)
    try:
        count = connection.execute("SELECT COUNT(*) FROM sample_nodes").fetchone()[0]
        state_count = connection.execute("SELECT COUNT(*) FROM app_state").fetchone()[0]
        event_count = connection.execute("SELECT COUNT(*) FROM app_events").fetchone()[0]
        current_schema_version = schema_version(connection)
    finally:
        connection.close()

    return {
        "database_path": str(config["DB_PATH"]),
        "sample_node_count": count,
        "app_state_count": state_count,
        "app_event_count": event_count,
        "schema_version": current_schema_version,
    }
