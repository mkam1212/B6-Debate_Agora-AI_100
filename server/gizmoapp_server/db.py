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
    7: """
CREATE TABLE account_users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE account_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
CREATE INDEX account_sessions_user ON account_sessions(user_id);
CREATE TABLE account_challenges (
    id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    email TEXT NOT NULL,
    user_id TEXT REFERENCES account_users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at INTEGER NOT NULL
);
CREATE TABLE account_rate_limits (
    bucket TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
""",
    8: """
ALTER TABLE debate_sessions ADD COLUMN draft_attachments_json TEXT NOT NULL DEFAULT '[]';
CREATE TABLE debate_attachments (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    mime TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    original BLOB NOT NULL,
    preview BLOB,
    extracted_text TEXT NOT NULL DEFAULT '',
    text_status TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE INDEX debate_attachments_owner ON debate_attachments(owner_id);
CREATE TABLE debate_attachment_links (
    attachment_id TEXT NOT NULL REFERENCES debate_attachments(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES debate_sessions(id) ON DELETE CASCADE,
    PRIMARY KEY (attachment_id, session_id)
);
""",
    9: """
CREATE TABLE account_wallets (
    user_id TEXT PRIMARY KEY REFERENCES account_users(id) ON DELETE CASCADE,
    invite_code TEXT NOT NULL UNIQUE,
    balance INTEGER NOT NULL CHECK (balance >= 0),
    spent INTEGER NOT NULL DEFAULT 0 CHECK (spent >= 0)
);
CREATE TABLE account_referrals (
    invitee_id TEXT PRIMARY KEY REFERENCES account_users(id) ON DELETE CASCADE,
    inviter_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    reward INTEGER NOT NULL CHECK (reward > 0),
    created_at INTEGER NOT NULL,
    CHECK (invitee_id != inviter_id)
);
CREATE TABLE account_usage_entries (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    delta INTEGER NOT NULL,
    reference TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (user_id, kind, reference)
);
CREATE INDEX account_usage_user ON account_usage_entries(user_id, created_at);
CREATE TABLE account_reply_reservations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('pending', 'completed', 'refunded')),
    created_at INTEGER NOT NULL
);
""",
    10: """
ALTER TABLE account_users ADD COLUMN avatar_color TEXT NOT NULL DEFAULT '#4667a5';
""",
    11: """
CREATE TABLE account_email_invites (
    id TEXT PRIMARY KEY,
    inviter_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('sending','pending','failed','accepted')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    invitee_id TEXT REFERENCES account_users(id) ON DELETE SET NULL,
    UNIQUE (inviter_id,email)
);
CREATE INDEX account_email_invites_owner ON account_email_invites(inviter_id,created_at);
""",
    12: """
CREATE TABLE debate_web_sources (
    session_id TEXT NOT NULL REFERENCES debate_sessions(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    excerpt TEXT NOT NULL DEFAULT '',
    request_scope TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (session_id, source_id),
    UNIQUE (session_id, url)
);
CREATE INDEX debate_web_sources_recent ON debate_web_sources(session_id, fetched_at DESC);
""",
}
SCHEMA_MIGRATIONS[13] = """
ALTER TABLE debate_sessions ADD COLUMN owner_id TEXT REFERENCES account_users(id);
ALTER TABLE debate_sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE debate_sessions ADD COLUMN share_hash TEXT;
ALTER TABLE debate_sessions ADD COLUMN title TEXT NOT NULL DEFAULT '';
ALTER TABLE debate_discussions ADD COLUMN owner_id TEXT REFERENCES account_users(id);
ALTER TABLE account_reply_reservations ADD COLUMN session_id TEXT;
CREATE UNIQUE INDEX account_session_reply_pending ON account_reply_reservations(session_id) WHERE state='pending' AND session_id IS NOT NULL;
CREATE INDEX debate_sessions_owner ON debate_sessions(owner_id, created_at DESC, id);
CREATE TABLE debate_legacy_imports (
    user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    legacy_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES debate_sessions(id) ON DELETE CASCADE,
    PRIMARY KEY(user_id, legacy_id)
);
CREATE TABLE debate_session_summaries (
    session_id TEXT PRIMARY KEY REFERENCES debate_sessions(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    source_revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE account_credit_requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','declined')),
    created_at INTEGER NOT NULL,
    resolved_at INTEGER
);
CREATE TABLE account_admins (
    user_id TEXT PRIMARY KEY REFERENCES account_users(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX account_credit_request_pending ON account_credit_requests(user_id) WHERE state='pending';
CREATE TABLE debate_feedback (
    session_id TEXT NOT NULL REFERENCES debate_sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    helpful INTEGER NOT NULL CHECK(helpful IN (0,1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(session_id,user_id)
);
"""
SCHEMA_MIGRATIONS[14] = "ALTER TABLE debate_sessions ADD COLUMN legacy_disabled INTEGER NOT NULL DEFAULT 0;"
SCHEMA_MIGRATIONS[15] = """
CREATE TABLE debate_reply_commits (
    reservation_id TEXT PRIMARY KEY REFERENCES account_reply_reservations(id),
    session_id TEXT NOT NULL REFERENCES debate_sessions(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    message_index INTEGER NOT NULL,
    message_json TEXT NOT NULL,
    UNIQUE(session_id, revision)
);
CREATE INDEX debate_attachments_created ON debate_attachments(created_at);
"""
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
