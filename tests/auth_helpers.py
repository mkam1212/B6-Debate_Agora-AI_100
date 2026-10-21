"""Authenticated clients for provider and attachment tests; no authentication bypass."""
import secrets
import time

from server.gizmoapp_server.account_api import _digest
from server.gizmoapp_server.db import get_db


def authenticated_client(app):
    client = app.test_client()
    token = secrets.token_urlsafe(24)
    user_id = secrets.token_hex(16)
    with app.app_context():
        connection = get_db()
        connection.execute("INSERT INTO account_users (id,name,email,password_hash,created_at) VALUES (?,?,?,?,?)", (user_id, "Test participant", f"{user_id}@example.com", "fixture-unused-password-hash", int(time.time())))
        connection.execute("INSERT INTO account_sessions VALUES (?,?,?)", (_digest(token), user_id, int(time.time()) + 3600))
        connection.commit()
    with client.session_transaction() as session:
        session["account_token"] = token
        session["csrf"] = secrets.token_urlsafe(24)
    return client
