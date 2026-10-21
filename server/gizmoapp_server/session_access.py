"""Account-owned conversations and revocable, read-only sharing."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets

from flask import jsonify, request

from .account_api import AccountError, _require_user, current_user, _rate_limit
from .config import scoped_path
from .db import WRITE_LOCK, get_db
from .debate_sessions import create_session, fetch_session, _now

ID = re.compile(r"^[a-f0-9]{32}$")


def token_matches(stored, token):
    return bool(stored and isinstance(token, str) and ID.fullmatch(token)
                and hmac.compare_digest(stored, hashlib.sha256(token.encode()).hexdigest()))


def require_session(session_id, *, edit=False):
    saved = fetch_session(get_db(), session_id) if ID.fullmatch(session_id) else None
    if saved is None:
        raise AccountError("This conversation is unavailable.", 404)
    user = current_user()
    if user and saved["ownerId"] == user["id"]:
        return saved, "owner"
    if not edit:
        row = get_db().execute("SELECT share_hash FROM debate_sessions WHERE id=?", (session_id,)).fetchone()
        token = request.headers.get("X-Debate-Share") or request.args.get("share", "")
        if token_matches(row["share_hash"], token):
            return saved, "viewer"
        # Old capability URLs keep working for reading only. They can never grant ownership.
        if saved["ownerId"] is None and not saved["legacyDisabled"]:
            return saved, "legacy"
    raise AccountError("This conversation is unavailable, or its sharing link has changed.", 404)


def summary_fingerprint(saved):
    data = {"question": saved["question"], "participants": saved["participants"], "messages": saved["messages"]}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def saved_summary(saved):
    row = get_db().execute("SELECT * FROM debate_session_summaries WHERE session_id=?", (saved["id"],)).fetchone()
    if not row:
        return None
    return {"summary": row["body"], "sourceRevision": row["source_revision"],
            "summarizedMessageCount": row["message_count"], "updatedAt": row["updated_at"],
            "isStale": row["content_hash"] != summary_fingerprint(saved)}


def session_response(saved, access):
    result = {key: value for key, value in saved.items() if key != "ownerId"}
    result.update(access=access, summary=saved_summary(saved))
    if access == "owner":
        old = get_db().execute("SELECT s.legacy_disabled FROM debate_legacy_imports i JOIN debate_sessions s ON s.id=i.legacy_id WHERE i.session_id=? AND i.user_id=?", (saved["id"], saved["ownerId"])).fetchone()
        result["legacyLinkActive"] = bool(old is not None and not old["legacy_disabled"])
        feedback = get_db().execute("SELECT helpful FROM debate_feedback WHERE session_id=? AND user_id=?", (saved["id"], saved["ownerId"])).fetchone()
        result["feedback"] = bool(feedback["helpful"]) if feedback else None
    if access != "owner":
        result.update(draft="", draftAttachments=[], pendingUserMessages=[])
    return result


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise AccountError("Please try again.")
    return data


def register_session_access_routes(app):
    prefix = app.config["URL_PREFIX"]

    @app.before_request
    def protect_previous_discussion_api():
        base = scoped_path(prefix, "api/debate/discussions")
        if request.path == base or request.path.startswith(base + "/"):
            user = _require_user()
            suffix = request.path[len(base):].strip("/")
            if suffix:
                row = get_db().execute("SELECT owner_id FROM debate_discussions WHERE id=?", (suffix.split("/")[0],)).fetchone()
                if not row or row["owner_id"] != user["id"]:
                    raise AccountError("This discussion is unavailable.", 404)
                if request.method == 'POST' and suffix.split('/')[-1] in {'run', 'continue'}:
                    raise AccountError('This older generation endpoint has been retired. Start a debate in the app.', 410)

    @app.after_request
    def private_conversations(response):
        if request.path.startswith(scoped_path(prefix, "api/debate/sessions")):
            response.headers["Cache-Control"] = "private, no-store"
            response.vary.add("Cookie")
            response.vary.add("X-Debate-Share")
        if request.args.get("share"):
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get(scoped_path(prefix, "api/debate/sessions"))
    def owned_sessions():
        user = _require_user()
        query = request.args.get("q", "").strip()[:200]
        archived = request.args.get("archived") == "1"
        try:
            offset = max(0, min(100_000, int(request.args.get("offset", 0))))
        except ValueError:
            raise AccountError("Invalid page.") from None
        rows = get_db().execute(
            "SELECT id,question,title,debate_started,created_at,updated_at,archived FROM debate_sessions s "
            "WHERE owner_id=? AND archived=? AND (?='' OR instr(lower(title || ' ' || question),lower(?))>0 OR EXISTS "
            "(SELECT 1 FROM json_each(s.messages_json) m WHERE instr(lower(json_extract(m.value,'$.body')),lower(?))>0)) "
            "ORDER BY created_at DESC,id DESC LIMIT 51 OFFSET ?", (user["id"], archived, query, query, query, offset),
        ).fetchall()
        return jsonify(sessions=[{"id": r["id"], "question": r["title"] or r["question"], "started": bool(r["debate_started"]),
                                  "createdAt": r["created_at"], "updated": r["updated_at"], "archived": bool(r["archived"])} for r in rows[:50]],
                       hasMore=len(rows) > 50, nextOffset=offset + min(len(rows), 50))

    @app.post(scoped_path(prefix, "api/debate/sessions/import"))
    def import_legacy_sessions():
        user = _require_user()
        data = _body()
        ids = data.get("sessionIds", [])
        if not isinstance(ids, list) or len(ids) > 40 or any(not isinstance(i, str) or not ID.fullmatch(i) for i in ids):
            raise AccountError("Invalid conversation list.")
        _rate_limit(f"legacy-import:{user['id']}", 10, 3600)
        connection = get_db()
        imported = {}
        with WRITE_LOCK:
            connection.execute("BEGIN IMMEDIATE")
            for old_id in dict.fromkeys(ids):
                old = fetch_session(connection, old_id)
                if not old or old["ownerId"] is not None:
                    continue
                prior = connection.execute("SELECT session_id FROM debate_legacy_imports WHERE user_id=? AND legacy_id=?", (user["id"], old_id)).fetchone()
                if prior:
                    imported[old_id] = prior["session_id"]
                    continue
                if old["legacyDisabled"]:
                    continue
                private = create_session(connection, old, user["id"], commit=False)
                new_id = private["id"]
                connection.execute("INSERT INTO debate_legacy_imports VALUES (?,?,?)", (user["id"], old_id, new_id))
                connection.execute("UPDATE debate_sessions SET created_at=?,updated_at=? WHERE id=?", (old["createdAt"], old["updatedAt"], new_id))
                connection.execute("INSERT INTO debate_attachment_links SELECT attachment_id,? FROM debate_attachment_links WHERE session_id=?", (new_id, old_id))
                connection.execute("INSERT INTO debate_web_sources SELECT ?,source_id,url,title,excerpt,request_scope,fetched_at FROM debate_web_sources WHERE session_id=?", (new_id, old_id))
                imported[old_id] = new_id
            connection.commit()
        return jsonify(imported=imported)

    @app.route(scoped_path(prefix, "api/debate/sessions/<session_id>/sharing"), methods=["GET", "POST", "DELETE"])
    def sharing(session_id):
        _require_user()
        require_session(session_id, edit=True)
        connection = get_db()
        if request.method == "GET":
            return jsonify(enabled=bool(connection.execute("SELECT share_hash FROM debate_sessions WHERE id=?", (session_id,)).fetchone()[0]))
        token = secrets.token_hex(16) if request.method == "POST" else None
        with WRITE_LOCK:
            connection.execute("UPDATE debate_sessions SET share_hash=? WHERE id=?", (hashlib.sha256(token.encode()).hexdigest() if token else None, session_id))
            connection.commit()
        return jsonify(enabled=bool(token), **({"token": token} if token else {}))

    @app.route(scoped_path(prefix, "api/debate/sessions/<session_id>/manage"), methods=["PATCH", "DELETE"])
    def manage_session(session_id):
        _require_user()
        require_session(session_id, edit=True)
        data = _body() if request.method == "PATCH" else {}
        connection = get_db()
        with WRITE_LOCK:
            if request.method == "DELETE":
                attachments = [r[0] for r in connection.execute("SELECT attachment_id FROM debate_attachment_links WHERE session_id=?", (session_id,))]
                connection.execute("UPDATE debate_sessions SET legacy_disabled=1 WHERE id IN (SELECT legacy_id FROM debate_legacy_imports WHERE session_id=?) AND owner_id IS NULL", (session_id,))
                connection.execute("DELETE FROM debate_sessions WHERE id=?", (session_id,))
                for attachment_id in attachments:
                    connection.execute("DELETE FROM debate_attachments WHERE id=? AND NOT EXISTS (SELECT 1 FROM debate_attachment_links WHERE attachment_id=?)", (attachment_id, attachment_id))
            elif data.get("retireLegacyLink") is True:
                old = connection.execute("SELECT legacy_id FROM debate_legacy_imports WHERE session_id=?", (session_id,)).fetchone()
                if old:
                    connection.execute("UPDATE debate_sessions SET legacy_disabled=1 WHERE id=? AND owner_id IS NULL", (old["legacy_id"],))
            elif "archived" in data and type(data["archived"]) is bool:
                connection.execute("UPDATE debate_sessions SET archived=? WHERE id=?", (int(data["archived"]), session_id))
            elif isinstance(data.get("title"), str) and 0 < len(data["title"].strip()) <= 2000:
                connection.execute("UPDATE debate_sessions SET title=?,revision=revision+1 WHERE id=?", (data["title"].strip(), session_id))
            else:
                raise AccountError("Enter a title, or choose an archive option.")
            connection.commit()
        return jsonify(ok=True)

    @app.post(scoped_path(prefix, "api/debate/sessions/<session_id>/feedback"))
    def conversation_feedback(session_id):
        user = _require_user()
        require_session(session_id, edit=True)
        data = _body()
        if type(data.get("helpful")) is not bool:
            raise AccountError("Choose whether this was useful.")
        with WRITE_LOCK:
            get_db().execute("INSERT INTO debate_feedback VALUES (?,?,?,?) ON CONFLICT(session_id,user_id) "
                             "DO UPDATE SET helpful=excluded.helpful,updated_at=excluded.updated_at",
                             (session_id, user["id"], int(data["helpful"]), _now()))
            get_db().commit()
        return jsonify(ok=True)
