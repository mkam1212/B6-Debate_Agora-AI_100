"""SQLite persistence for discussions and immutable analysis versions."""

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


def _load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def new_run(state: str = "idle") -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "state": state,
        "completedSteps": [],
        "currentStep": None,
        "error": None,
        "cancelRequested": False,
        "continueWithAssumptions": False,
        "attempts": 0,
        "startedAt": None,
        "updatedAt": _now(),
    }


def _version_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "version": int(row["version"]),
        "status": row["status"],
        "question": _load(row["question_json"], {}),
        "perspectives": _load(row["perspectives_json"], []),
        "sources": _load(row["sources_json"], []),
        "framing": _load(row["framing_json"], None),
        "contributions": _load(row["contributions_json"], []),
        "synthesis": _load(row["synthesis_json"], None),
        "followUps": _load(row["followups_json"], []),
        "run": _load(row["run_json"], new_run()),
        "reflection": row["reflection"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_discussions(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, title, status, mode, current_version, created_at, updated_at
        FROM debate_discussions
        ORDER BY updated_at DESC, id DESC
        LIMIT 200
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "mode": row["mode"],
            "currentVersion": int(row["current_version"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def fetch_discussion(
    connection: sqlite3.Connection,
    discussion_id: str,
    version: int | None = None,
) -> dict[str, Any] | None:
    discussion = connection.execute(
        """
        SELECT id, title, status, mode, current_version, created_at, updated_at
        FROM debate_discussions WHERE id = ?
        """,
        (discussion_id,),
    ).fetchone()
    if discussion is None:
        return None

    current_version = int(discussion["current_version"])
    selected_version = current_version if version is None else version
    selected = connection.execute(
        """
        SELECT * FROM debate_versions WHERE discussion_id = ? AND version = ?
        """,
        (discussion_id, selected_version),
    ).fetchone()
    if selected is None:
        return None

    versions = connection.execute(
        """
        SELECT version, status, created_at, updated_at
        FROM debate_versions WHERE discussion_id = ? ORDER BY version
        """,
        (discussion_id,),
    ).fetchall()
    result = {
        "id": discussion["id"],
        "title": discussion["title"],
        "status": selected["status"],
        "mode": discussion["mode"],
        "currentVersion": current_version,
        "isCurrentVersion": selected_version == current_version,
        "versions": [
            {
                "version": int(row["version"]),
                "status": row["status"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in versions
        ],
        "createdAt": discussion["created_at"],
        "updatedAt": discussion["updated_at"],
    }
    result.update(_version_payload(selected))
    return result


def create_discussion(
    connection: sqlite3.Connection,
    question: dict[str, Any],
    perspectives: list[dict[str, str]],
    sources: list[dict[str, str]],
) -> dict[str, Any]:
    discussion_id = uuid.uuid4().hex
    now = _now()
    title = question["title"]
    run = new_run()
    with WRITE_LOCK:
        connection.execute(
            """
            INSERT INTO debate_discussions (id, title, status, mode, current_version, created_at, updated_at)
            VALUES (?, ?, 'draft', 'live', 1, ?, ?)
            """,
            (discussion_id, title, now, now),
        )
        connection.execute(
            """
            INSERT INTO debate_versions (
                discussion_id, version, status, question_json, perspectives_json, sources_json,
                framing_json, contributions_json, synthesis_json, followups_json, run_json,
                reflection, created_at, updated_at
            ) VALUES (?, 1, 'draft', ?, ?, ?, NULL, '[]', NULL, '[]', ?, '', ?, ?)
            """,
            (discussion_id, _dump(question), _dump(perspectives), _dump(sources), _dump(run), now, now),
        )
        connection.commit()
    return fetch_discussion(connection, discussion_id) or {}


def save_draft(
    connection: sqlite3.Connection,
    discussion_id: str,
    version: int,
    question: dict[str, Any],
    perspectives: list[dict[str, str]],
    sources: list[dict[str, str]],
) -> bool:
    now = _now()
    with WRITE_LOCK:
        cursor = connection.execute(
            """
            UPDATE debate_versions
            SET question_json = ?, perspectives_json = ?, sources_json = ?, updated_at = ?
            WHERE discussion_id = ? AND version = ? AND status = 'draft'
            """,
            (_dump(question), _dump(perspectives), _dump(sources), now, discussion_id, version),
        )
        if cursor.rowcount:
            connection.execute(
                "UPDATE debate_discussions SET title = ?, status = 'draft', updated_at = ? WHERE id = ? AND current_version = ?",
                (question["title"], now, discussion_id, version),
            )
            connection.commit()
            return True
        connection.rollback()
        return False


def create_version(
    connection: sqlite3.Connection,
    discussion_id: str,
    base_version: int,
    question: dict[str, Any],
    perspectives: list[dict[str, str]],
    sources: list[dict[str, str]],
    follow_ups: list[str],
) -> dict[str, Any] | None:
    now = _now()
    with WRITE_LOCK:
        parent = connection.execute(
            "SELECT 1 FROM debate_versions WHERE discussion_id = ? AND version = ?",
            (discussion_id, base_version),
        ).fetchone()
        if parent is None:
            connection.rollback()
            return None
        latest = connection.execute(
            "SELECT MAX(version) FROM debate_versions WHERE discussion_id = ?", (discussion_id,)
        ).fetchone()[0]
        next_version = int(latest or 0) + 1
        connection.execute(
            """
            INSERT INTO debate_versions (
                discussion_id, version, status, question_json, perspectives_json, sources_json,
                framing_json, contributions_json, synthesis_json, followups_json, run_json,
                reflection, created_at, updated_at
            ) VALUES (?, ?, 'draft', ?, ?, ?, NULL, '[]', NULL, ?, ?, '', ?, ?)
            """,
            (
                discussion_id,
                next_version,
                _dump(question),
                _dump(perspectives),
                _dump(sources),
                _dump(follow_ups),
                _dump(new_run()),
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE debate_discussions SET title = ?, status = 'draft', current_version = ?, updated_at = ? WHERE id = ?",
            (question["title"], next_version, now, discussion_id),
        )
        connection.commit()
    return fetch_discussion(connection, discussion_id, next_version)


def update_run(
    connection: sqlite3.Connection,
    discussion_id: str,
    version: int,
    changes: dict[str, Any],
    status: str | None = None,
) -> bool:
    with WRITE_LOCK:
        row = connection.execute(
            "SELECT run_json, status FROM debate_versions WHERE discussion_id = ? AND version = ?",
            (discussion_id, version),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        run = _load(row["run_json"], new_run())
        run.update(changes)
        run["updatedAt"] = _now()
        next_status = status or row["status"]
        now = _now()
        connection.execute(
            "UPDATE debate_versions SET run_json = ?, status = ?, updated_at = ? WHERE discussion_id = ? AND version = ?",
            (_dump(run), next_status, now, discussion_id, version),
        )
        connection.execute(
            "UPDATE debate_discussions SET status = ?, updated_at = ? WHERE id = ? AND current_version = ?",
            (next_status, now, discussion_id, version),
        )
        connection.commit()
        return True


def save_framing(
    connection: sqlite3.Connection, discussion_id: str, version: int, framing: dict[str, Any]
) -> None:
    _save_json_column(connection, discussion_id, version, "framing_json", framing)


def save_synthesis(
    connection: sqlite3.Connection, discussion_id: str, version: int, synthesis: dict[str, Any]
) -> None:
    _save_json_column(connection, discussion_id, version, "synthesis_json", synthesis)


def save_contribution(
    connection: sqlite3.Connection, discussion_id: str, version: int, contribution: dict[str, Any]
) -> None:
    with WRITE_LOCK:
        row = connection.execute(
            "SELECT contributions_json FROM debate_versions WHERE discussion_id = ? AND version = ?",
            (discussion_id, version),
        ).fetchone()
        if row is None:
            connection.rollback()
            raise LookupError("The requested discussion version no longer exists.")
        contributions = _load(row["contributions_json"], [])
        contributions = [item for item in contributions if item.get("id") != contribution["id"]]
        contributions.append(contribution)
        now = _now()
        connection.execute(
            "UPDATE debate_versions SET contributions_json = ?, updated_at = ? WHERE discussion_id = ? AND version = ?",
            (_dump(contributions), now, discussion_id, version),
        )
        connection.execute(
            "UPDATE debate_discussions SET updated_at = ? WHERE id = ? AND current_version = ?",
            (now, discussion_id, version),
        )
        connection.commit()


def _save_json_column(
    connection: sqlite3.Connection,
    discussion_id: str,
    version: int,
    column: str,
    value: dict[str, Any],
) -> None:
    if column not in {"framing_json", "synthesis_json"}:
        raise ValueError("Unsupported discussion data column.")
    now = _now()
    with WRITE_LOCK:
        connection.execute(
            f"UPDATE debate_versions SET {column} = ?, updated_at = ? WHERE discussion_id = ? AND version = ?",
            (_dump(value), now, discussion_id, version),
        )
        connection.execute(
            "UPDATE debate_discussions SET updated_at = ? WHERE id = ? AND current_version = ?",
            (now, discussion_id, version),
        )
        connection.commit()


def save_reflection(
    connection: sqlite3.Connection, discussion_id: str, version: int, reflection: str
) -> bool:
    now = _now()
    with WRITE_LOCK:
        cursor = connection.execute(
            "UPDATE debate_versions SET reflection = ?, updated_at = ? WHERE discussion_id = ? AND version = ? AND status = 'completed'",
            (reflection, now, discussion_id, version),
        )
        if cursor.rowcount:
            connection.execute(
                "UPDATE debate_discussions SET updated_at = ? WHERE id = ? AND current_version = ?",
                (now, discussion_id, version),
            )
            connection.commit()
            return True
        connection.rollback()
        return False


def delete_discussion(connection: sqlite3.Connection, discussion_id: str) -> bool:
    with WRITE_LOCK:
        cursor = connection.execute("DELETE FROM debate_discussions WHERE id = ?", (discussion_id,))
        connection.commit()
        return bool(cursor.rowcount)
