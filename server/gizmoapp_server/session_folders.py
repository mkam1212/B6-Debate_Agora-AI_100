"""Private filing metadata, independent of live conversation revisions."""
from __future__ import annotations

import uuid

from flask import jsonify, request

from .account_api import AccountError, _require_user
from .config import scoped_path
from .db import WRITE_LOCK, get_db
from .debate_sessions import _now
from .session_access import ID, _body, require_session


def require_folder(connection, folder_id, owner_id):
    row = connection.execute(
        "SELECT * FROM debate_folders WHERE id=? AND owner_id=?", (folder_id, owner_id)
    ).fetchone() if isinstance(folder_id, str) and ID.fullmatch(folder_id) else None
    if row is None:
        raise AccountError("This folder is unavailable. Choose another folder.", 404)
    return row


def folder_name(data):
    name = data.get("name")
    if not isinstance(name, str) or not 0 < len(name.strip()) <= 48 or any(ord(c) < 32 for c in name):
        raise AccountError("Enter a folder name of up to 48 characters.")
    return name.strip()


def register_folder_routes(app):
    prefix = app.config["URL_PREFIX"]

    @app.route(scoped_path(prefix, "api/debate/folders"), methods=["GET", "POST"])
    def folders():
        user = _require_user()
        connection = get_db()
        if request.method == "GET":
            rows = connection.execute(
                "SELECT f.id,f.name,f.created_at,count(s.id) AS count,max(s.created_at) AS latest_conversation_at FROM debate_folders f "
                "LEFT JOIN debate_sessions s ON s.folder_id=f.id AND s.owner_id=f.owner_id AND s.archived=0 "
                "WHERE f.owner_id=? GROUP BY f.id ORDER BY coalesce(max(s.created_at),f.created_at) DESC,f.id DESC", (user["id"],)
            ).fetchall()
            response = jsonify(folders=[{"id": r["id"], "name": r["name"], "count": r["count"],
                                         "createdAt": r["created_at"], "latestConversationAt": r["latest_conversation_at"]} for r in rows])
        else:
            name = folder_name(_body())
            folder_id = uuid.uuid4().hex
            with WRITE_LOCK:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT count(*) FROM debate_folders WHERE owner_id=?", (user["id"],)).fetchone()[0] >= 100:
                    connection.rollback()
                    raise AccountError("You have 100 folders. Rename or remove one to make room.")
                connection.execute("INSERT INTO debate_folders VALUES (?,?,?,?)", (folder_id, user["id"], name, _now()))
                connection.commit()
            response = jsonify(folder={"id": folder_id, "name": name, "count": 0})
            response.status_code = 201
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.route(scoped_path(prefix, "api/debate/folders/<folder_id>"), methods=["PATCH", "DELETE"])
    def manage_folder(folder_id):
        user = _require_user()
        name = folder_name(_body()) if request.method == "PATCH" else None
        connection = get_db()
        with WRITE_LOCK:
            connection.execute("BEGIN IMMEDIATE")
            require_folder(connection, folder_id, user["id"])
            if request.method == "DELETE":
                # ON DELETE SET NULL keeps conversations, attachments and viewing links intact.
                connection.execute("DELETE FROM debate_folders WHERE id=? AND owner_id=?", (folder_id, user["id"]))
            else:
                connection.execute("UPDATE debate_folders SET name=? WHERE id=? AND owner_id=?", (name, folder_id, user["id"]))
            connection.commit()
        return jsonify(ok=True)

    @app.patch(scoped_path(prefix, "api/debate/sessions/<session_id>/folder"))
    def file_session(session_id):
        user = _require_user()
        data = _body()
        if "folderId" not in data:
            raise AccountError("Choose a folder, or Ungrouped.")
        folder_id = data["folderId"]
        connection = get_db()
        with WRITE_LOCK:
            connection.execute("BEGIN IMMEDIATE")
            require_session(session_id, edit=True)
            if folder_id is not None:
                require_folder(connection, folder_id, user["id"])
            # Filing must not invalidate a streamed reply or an in-flight draft save.
            connection.execute("UPDATE debate_sessions SET folder_id=? WHERE id=? AND owner_id=?", (folder_id, session_id, user["id"]))
            connection.commit()
        return jsonify(ok=True, folderId=folder_id)
