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
        "title": row["title"] or row["question"],
        "customTitle": row["title"],
        "legacyDisabled": bool(row["legacy_disabled"]),
        "sharingEnabled": bool(row["share_hash"]),
        "participants": _load(row["participants_json"], []),
        "messages": _load(row["messages_json"], []),
        "pendingUserMessages": _load(row["pending_user_messages_json"], []),
        "draft": row["draft"],
        "draftAttachments": _load(row["draft_attachments_json"], []),
        "debateStarted": bool(row["debate_started"]),
        "paused": bool(row["paused"]),
        "revision": int(row["revision"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "ownerId": row["owner_id"],
        "archived": bool(row["archived"]),
        "folderId": row["folder_id"],
    }


def fetch_session(connection: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM debate_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    return _session_payload(row) if row is not None else None


def create_session(connection: sqlite3.Connection, snapshot: dict[str, Any], owner_id: str | None = None, *, commit=True, folder_id=None) -> dict[str, Any]:
    session_id = uuid.uuid4().hex
    now = _now()
    with WRITE_LOCK:
        if folder_id is not None:
            if commit:
                connection.execute("BEGIN IMMEDIATE")
            from .session_folders import require_folder
            require_folder(connection, folder_id, owner_id)
        connection.execute(
            """
            INSERT INTO debate_sessions (
                id, question, participants_json, messages_json, pending_user_messages_json, draft,
                debate_started, paused, created_at, updated_at, draft_attachments_json, owner_id, folder_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _dump(snapshot.get("draftAttachments", [])),
                owner_id,
                folder_id,
            ),
        )
        if commit:
            connection.commit()
    return fetch_session(connection, session_id) or {}


def update_session(
    connection: sqlite3.Connection,
    session_id: str,
    snapshot: dict[str, Any],
    expected_revision: int,
) -> dict[str, Any] | None:
    from .debate_replies import merge_committed_replies, only_reply_revisions
    now = _now()
    with WRITE_LOCK:
        connection.execute("BEGIN IMMEDIATE")
        current = fetch_session(connection, session_id)
        if current is None or not only_reply_revisions(connection, session_id, expected_revision, current['revision']):
            connection.rollback()
            return None
        original_ids = {m.get('id') for m in snapshot['messages']}
        snapshot = {**snapshot, 'messages': merge_committed_replies(connection, session_id, snapshot['messages'])}
        if current['messages'] and current['messages'][-1].get('roundEnd') and current['messages'][-1].get('id') not in original_ids and not snapshot['pendingUserMessages']:
            snapshot['paused'] = True
        from .debate_api import MAX_SESSION_MESSAGES, MAX_SESSION_SNAPSHOT_BYTES
        if len(snapshot['messages']) + len(snapshot['pendingUserMessages']) > MAX_SESSION_MESSAGES or len(_dump(snapshot).encode()) > MAX_SESSION_SNAPSHOT_BYTES:
            connection.rollback()
            raise ValueError('This conversation is full. Start a new debate to keep going.')
        cursor = connection.execute(
            """
            UPDATE debate_sessions
            SET question = ?, participants_json = ?, messages_json = ?, pending_user_messages_json = ?, draft = ?,
                debate_started = ?, paused = ?, updated_at = ?, draft_attachments_json = ?, revision = revision + 1
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
                _dump(snapshot.get("draftAttachments", [])),
                session_id,
                current['revision'],
            ),
        )
        if cursor.rowcount == 0:
            connection.rollback()
            return None
        session = fetch_session(connection, session_id)
        connection.commit()
    return session
