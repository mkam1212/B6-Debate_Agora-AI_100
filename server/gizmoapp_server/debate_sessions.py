"""SQLite persistence for the three-screen Debate chat sessions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from .db import WRITE_LOCK


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _load(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _session_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "question": row["question"],
        "participants": _load(row["participants_json"], []),
        "messages": _load(row["messages_json"], []),
        "pendingUserMessages": _load(row["pending_user_messages_json"], []),
        "draft": row["draft"],
        "debateStarted": bool(row["debate_started"]),
        "paused": bool(row["paused"]),
        "revision": int(row["revision"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def fetch_session(connection: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM debate_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    return _session_payload(row) if row is not None else None


def create_session(connection: sqlite3.Connection, snapshot: dict[str, Any]) -> dict[str, Any]:
    session_id = uuid.uuid4().hex
    now = _now()
    with WRITE_LOCK:
        connection.execute(
            """
            INSERT INTO debate_sessions (
                id, question, participants_json, messages_json, pending_user_messages_json, draft,
                debate_started, paused, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                snapshot["question"],
                _dump(snapshot["participants"]),
                _dump(snapshot["messages"]),
                _dump(snapshot["pendingUserMessages"]),
                snapshot["draft"],
                int(snapshot["debateStarted"]),
                int(snapshot["paused"]),
                now,
                now,
            ),
        )
        connection.commit()
    return fetch_session(connection, session_id) or {}


def update_session(
    connection: sqlite3.Connection,
    session_id: str,
    snapshot: dict[str, Any],
    expected_revision: int,
) -> dict[str, Any] | None:
    now = _now()
    with WRITE_LOCK:
        cursor = connection.execute(
            """
            UPDATE debate_sessions
            SET question = ?, participants_json = ?, messages_json = ?, pending_user_messages_json = ?, draft = ?,
                debate_started = ?, paused = ?, updated_at = ?, revision = revision + 1
            WHERE id = ? AND revision = ?
            """,
            (
                snapshot["question"],
                _dump(snapshot["participants"]),
                _dump(snapshot["messages"]),
                _dump(snapshot["pendingUserMessages"]),
                snapshot["draft"],
                int(snapshot["debateStarted"]),
                int(snapshot["paused"]),
                now,
                session_id,
                expected_revision,
            ),
        )
        if cursor.rowcount == 0:
            connection.rollback()
            return None
        session = fetch_session(connection, session_id)
        connection.commit()
    return session
