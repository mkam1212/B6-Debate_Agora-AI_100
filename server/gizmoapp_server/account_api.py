"""Email-verified accounts. Credentials and verification codes stay on the server."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .config import scoped_path
from .db import WRITE_LOCK, get_db
from .account_usage import ensure_wallet, award_invitation, usage_summary, invitation_history, request_credit, review_credit_request
from .avatar_colors import DEFAULT_AVATAR_COLOR, normalize_avatar_color
from .account_email_design import verification_email_content


class AccountError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def _digest(value):
    return hmac.new(str(current_app.secret_key).encode(), value.encode(), hashlib.sha256).hexdigest()


def _now():
    return int(time.time())


def _text(data, key, maximum=128):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > maximum:
        raise AccountError(f"Invalid {key}.")
    return value


def _email(data):
    email = _text(data, "email", 254).strip().casefold()
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", email):
        raise AccountError("Enter a valid email address.")
    return email


def _name(data):
    name = _text(data, "name", 48).strip()
    if not name or any(ord(char) < 32 for char in name):
        raise AccountError("Enter a name between 1 and 48 characters.")
    return name


def _password(data, key="password"):
    value = _text(data, key)
    if len(value) < 10:
        raise AccountError("Use a password with at least 10 characters.")
    return value


def current_user():
    token = session.get("account_token")
    if not isinstance(token, str):
        return None
    return get_db().execute(
        "SELECT u.* FROM account_users u JOIN account_sessions s ON u.id=s.user_id "
        "WHERE s.token_hash=? AND s.expires_at>?", (_digest(token), _now())
    ).fetchone()


CREDIT_REVIEWER_EMAIL = "guolinn@student.ubc.ca"


def is_credit_reviewer(user):
    return bool(user and user["email"].casefold() == CREDIT_REVIEWER_EMAIL
                and get_db().execute("SELECT 1 FROM account_admins WHERE user_id=?", (user["id"],)).fetchone())


def _public(user):
    return {**{key: user[key] for key in ("id", "name", "email")}, "avatarColor": user["avatar_color"],
            "isAdmin": is_credit_reviewer(user)} if user else None


def _avatar_color(data, fallback=DEFAULT_AVATAR_COLOR):
    try:
        return normalize_avatar_color(data.get("avatarColor", fallback))
    except ValueError as error:
        raise AccountError(str(error)) from None


def _require_user():
    user = current_user()
    if user is None:
        raise AccountError("Sign in to continue.", 401)
    return user


def _rate_limit(key, limit, seconds):
    connection = get_db()
    bucket = _digest(key)
    with WRITE_LOCK:
        connection.execute("DELETE FROM account_rate_limits WHERE expires_at<=?", (_now(),))
        connection.execute(
            "INSERT INTO account_rate_limits VALUES (?, 1, ?) "
            "ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1", (bucket, _now() + seconds)
        )
        count = connection.execute("SELECT attempts FROM account_rate_limits WHERE bucket=?", (bucket,)).fetchone()[0]
        connection.commit()
    if count > limit:
        raise AccountError("Too many attempts. Please try again later.", 429)


def _sign_in(user_id):
    connection = get_db()
    old = session.get("account_token")
    if isinstance(old, str):
        connection.execute("DELETE FROM account_sessions WHERE token_hash=?", (_digest(old),))
    token = secrets.token_urlsafe(32)
    connection.execute("DELETE FROM account_sessions WHERE expires_at<=?", (_now(),))
    connection.execute("INSERT INTO account_sessions VALUES (?, ?, ?)", (_digest(token), user_id, _now() + 30 * 86400))
    upload_owner = session.get("upload_owner")
    session.clear()
    if upload_owner:
        session["upload_owner"] = upload_owner
    session.update(account_token=token, csrf=secrets.token_urlsafe(32))
    session.permanent = True


def send_verification_email(email, code, purpose, challenge_id):
    api_key = current_app.config.get("RESEND_API_KEY")
    sender = current_app.config.get("RESEND_FROM_EMAIL")
    if not api_key or not sender:
        raise AccountError("Email verification is not configured yet.", 503)
    payload = json.dumps({
        "from": sender, "to": [email], **verification_email_content(code, purpose),
    }).encode()
    mail_request = Request("https://api.resend.com/emails", data=payload, method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
        "User-Agent": "DebateAgora/1.0", "Idempotency-Key": challenge_id,
    })
    try:
        with urlopen(mail_request, timeout=10) as response:
            if response.status not in (200, 201, 202):
                raise AccountError("The verification email could not be sent. Please try again.", 502)
    except (HTTPError, URLError, TimeoutError, OSError):
        # Provider responses can contain credentials or recipient data. Never return/log them.
        raise AccountError("The verification email could not be sent. Please try again.", 502) from None


def register_account_routes(app):
    prefix = app.config["URL_PREFIX"]
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

    @app.before_request
    def remember_invitation():
        code = request.args.get("invite", "")
        if code and len(code) <= 64:
            if get_db().execute("SELECT user_id FROM account_wallets WHERE invite_code=?", (code,)).fetchone():
                session["invite_code"] = code

    @app.after_request
    def private_account_responses(response):
        if request.path.startswith(scoped_path(prefix, "/api/account/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(AccountError)
    def account_error(error):
        if get_db().in_transaction:
            get_db().rollback()
        return jsonify(error=error.message, errors=[error.message]), error.status

    def payload():
        csrf = session.get("csrf")
        supplied = request.headers.get("X-CSRF-Token", "")
        if not isinstance(csrf, str) or not hmac.compare_digest(csrf.encode(), supplied.encode()):
            raise AccountError("Your session changed. Reopen this form and try again.", 403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise AccountError("Expected a JSON object.")
        return data

    @app.get(scoped_path(prefix, "/api/account/session"))
    def account_session():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        user = current_user()
        invitation = None
        if not user and session.get("invite_code"):
            owner = get_db().execute("SELECT u.name FROM account_wallets w JOIN account_users u ON u.id=w.user_id WHERE w.invite_code=?", (session["invite_code"],)).fetchone()
            if owner:
                invitation = {"name": owner["name"], "reward": app.config["ACCOUNT_INVITE_REWARD"]}
        return jsonify(user=_public(user), csrfToken=session["csrf"], usage=usage_summary(user["id"]) if user else None, invitation=invitation, starterAllowance=app.config["ACCOUNT_STARTER_REPLIES"])

    @app.post(scoped_path(prefix, "/api/account/credit-request"))
    def credit_request():
        user = _require_user()
        payload()
        _rate_limit(f"credit-request:{user['id']}", 10, 86400)
        request_credit(user["id"])
        return jsonify(usage=usage_summary(user["id"]))

    @app.route(scoped_path(prefix, "/api/account/credit-requests"), methods=["GET", "POST"])
    def review_credit_requests():
        user = _require_user()
        if not is_credit_reviewer(user):
            raise AccountError("This page is unavailable.", 404)
        if request.method == "POST":
            data = payload()
            request_id = data.get("id", "")
            if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id) or type(data.get("decline", False)) is not bool:
                raise AccountError("Choose a pending request.")
            try:
                changed = review_credit_request(request_id, data.get("replies", 50), decline=data.get("decline", False))
            except ValueError as error:
                raise AccountError(str(error)) from None
            return jsonify(ok=changed)
        rows = get_db().execute("SELECT r.id,r.created_at,u.name,u.avatar_color,w.balance FROM account_credit_requests r "
                                "JOIN account_users u ON u.id=r.user_id JOIN account_wallets w ON w.user_id=r.user_id "
                                "WHERE r.state='pending' ORDER BY r.created_at LIMIT 100").fetchall()
        return jsonify(requests=[{"id": r["id"], "createdAt": r["created_at"], "name": r["name"], "avatarColor": r["avatar_color"], "remaining": r["balance"]} for r in rows])

    @app.get(scoped_path(prefix, "/api/account/usage"))
    def account_usage():
        return jsonify(usage=usage_summary(_require_user()["id"]))

    @app.get(scoped_path(prefix, "/api/account/invites"))
    def account_invites():
        user = _require_user()
        period = request.args.get("period", "90")
        offset = request.args.get("offset", "0")
        if period not in ("30", "90", "all") or not re.fullmatch(r"[0-9]{1,6}", offset):
            raise AccountError("Invalid invitation history filter.")
        return jsonify(invitation_history(user["id"], 0 if period == "all" else int(period), int(offset)))

    @app.post(scoped_path(prefix, "/api/account/invites"))
    def account_send_invites():
        from .account_invites import send_email_invitations
        data, user = payload(), _require_user()
        _rate_limit(f"invites:{user['id']}", 12, 3600)
        _rate_limit(f"invites-ip:{request.remote_addr}", 30, 3600)
        return jsonify(send_email_invitations(user, data))

    @app.post(scoped_path(prefix, "/api/account/sign-in"))
    def account_sign_in():
        data = payload()
        email = _email(data)
        _rate_limit(f"login-ip:{request.remote_addr}", 40, 900)
        _rate_limit(f"login:{email}", 12, 900)
        password = _text(data, "password")
        user = get_db().execute("SELECT * FROM account_users WHERE email=?", (email,)).fetchone()
        # A fixed dummy hash keeps unknown-address checks on the same expensive code path.
        password_hash = user["password_hash"] if user else app.extensions["account_dummy_hash"]
        if not check_password_hash(password_hash, password) or user is None:
            raise AccountError("Email or password is incorrect.", 401)
        with WRITE_LOCK:
            _sign_in(user["id"])
            get_db().commit()
        return jsonify(user=_public(user), csrfToken=session["csrf"])

    @app.post(scoped_path(prefix, "/api/account/sign-out"))
    def account_sign_out():
        payload()
        token = session.get("account_token")
        with WRITE_LOCK:
            if isinstance(token, str):
                get_db().execute("DELETE FROM account_sessions WHERE token_hash=?", (_digest(token),))
            get_db().commit()
        session.clear()
        session["csrf"] = secrets.token_urlsafe(32)
        return jsonify(user=None, csrfToken=session["csrf"])

    @app.patch(scoped_path(prefix, "/api/account/profile"))
    def account_profile():
        data, user = payload(), _require_user()
        name, color = _name(data), _avatar_color(data, user["avatar_color"])
        with WRITE_LOCK:
            get_db().execute("UPDATE account_users SET name=?, avatar_color=? WHERE id=?", (name, color, user["id"]))
            get_db().commit()
        return jsonify(user=_public(current_user()))

    @app.patch(scoped_path(prefix, "/api/account/password"))
    def account_password():
        data, user = payload(), _require_user()
        _rate_limit(f"password:{user['id']}", 10, 900)
        if not check_password_hash(user["password_hash"], _text(data, "currentPassword")):
            raise AccountError("Current password is incorrect.", 401)
        password_hash = generate_password_hash(_password(data, "newPassword"))
        with WRITE_LOCK:
            get_db().execute("UPDATE account_users SET password_hash=? WHERE id=?", (password_hash, user["id"]))
            get_db().execute("DELETE FROM account_sessions WHERE user_id=?", (user["id"],))
            get_db().execute("DELETE FROM account_challenges WHERE user_id=? OR email=?", (user["id"], user["email"]))
            _sign_in(user["id"])
            get_db().commit()
        return jsonify(user=_public(user), csrfToken=session["csrf"])

    @app.post(scoped_path(prefix, "/api/account/verification"))
    def account_verification():
        data = payload()
        purpose = data.get("purpose")
        if not isinstance(purpose, str) or purpose not in {"register", "email", "reset"}:
            raise AccountError("Invalid verification request.")
        email = _email(data)
        _rate_limit(f"mail-ip:{request.remote_addr}", 30, 3600)
        _rate_limit(f"mail:{email}", 6, 3600)
        connection = get_db()
        existing = connection.execute("SELECT * FROM account_users WHERE email=?", (email,)).fetchone()
        user_id, pending = None, {}
        if purpose == "email":
            user = _require_user()
            _rate_limit(f"email-change:{user['id']}", 10, 900)
            if not check_password_hash(user["password_hash"], _text(data, "currentPassword")):
                raise AccountError("Current password is incorrect.", 401)
            if existing:
                raise AccountError("That email address is already in use.", 409)
            user_id = user["id"]
        elif purpose == "register":
            pending = {"name": _name(data), "password_hash": generate_password_hash(_password(data)), "avatarColor": _avatar_color(data)}
            inviter = connection.execute("SELECT user_id FROM account_wallets WHERE invite_code=?", (session.get("invite_code", ""),)).fetchone()
            if inviter:
                pending["inviter_id"] = inviter["user_id"]
        elif existing:
            user_id = existing["id"]
        challenge_id = secrets.token_urlsafe(24)
        # Do not disclose whether an address is registered through the public flows.
        if (purpose == "register" and existing) or (purpose == "reset" and not existing):
            return jsonify(challengeId=challenge_id, expiresIn=600)
        code = f"{secrets.randbelow(1_000_000):06d}"
        with WRITE_LOCK:
            connection.execute("DELETE FROM account_challenges WHERE expires_at<=? OR (email=? AND purpose=?)", (_now(), email, purpose))
            connection.execute("INSERT INTO account_challenges (id,purpose,email,user_id,code_hash,payload_json,expires_at) VALUES (?,?,?,?,?,?,?)", (
                challenge_id, purpose, email, user_id, _digest(f"{challenge_id}:{code}"), json.dumps(pending), _now() + 600,
            ))
            connection.commit()
        try:
            send_verification_email(email, code, purpose, challenge_id)
        except AccountError:
            with WRITE_LOCK:
                connection.execute("DELETE FROM account_challenges WHERE id=?", (challenge_id,))
                connection.commit()
            raise
        return jsonify(challengeId=challenge_id, expiresIn=600)

    @app.post(scoped_path(prefix, "/api/account/verify"))
    def account_verify():
        data = payload()
        challenge_id, code = _text(data, "challengeId", 64), _text(data, "code", 6)
        _rate_limit(f"verify-ip:{request.remote_addr}", 50, 900)
        connection = get_db()
        with WRITE_LOCK:
            # Serialize consumption across Gunicorn workers as well as threads.
            connection.execute("BEGIN IMMEDIATE")
            challenge = connection.execute("SELECT * FROM account_challenges WHERE id=?", (challenge_id,)).fetchone()
            if not challenge or challenge["expires_at"] <= _now() or challenge["attempts"] >= 5:
                raise AccountError("This code has expired. Request a new one.")
            if challenge["purpose"] == "email":
                if _require_user()["id"] != challenge["user_id"]:
                    raise AccountError("This verification belongs to another account.", 403)
            connection.execute("UPDATE account_challenges SET attempts=attempts+1 WHERE id=?", (challenge_id,))
            if not re.fullmatch(r"\d{6}", code) or not hmac.compare_digest(challenge["code_hash"], _digest(f"{challenge_id}:{code}")):
                connection.commit()
                raise AccountError("The code is incorrect. Please try again.")
            try:
                user_id = challenge["user_id"]
                if challenge["purpose"] == "register":
                    pending = json.loads(challenge["payload_json"])
                    user_id = secrets.token_hex(16)
                    connection.execute("INSERT INTO account_users (id,name,email,password_hash,created_at,avatar_color) VALUES (?,?,?,?,?,?)", (user_id, pending["name"], challenge["email"], pending["password_hash"], _now(), _avatar_color(pending)))
                    ensure_wallet(connection, user_id)
                    award_invitation(connection, user_id, pending.get("inviter_id"))
                elif challenge["purpose"] == "email":
                    connection.execute("UPDATE account_users SET email=? WHERE id=?", (challenge["email"], user_id))
                    connection.execute("DELETE FROM account_sessions WHERE user_id=?", (user_id,))
                else:
                    connection.execute("UPDATE account_users SET password_hash=? WHERE id=?", (generate_password_hash(_password(data, "newPassword")), user_id))
                    connection.execute("DELETE FROM account_sessions WHERE user_id=?", (user_id,))
                connection.execute("DELETE FROM account_challenges WHERE id=? OR user_id=? OR email=?", (challenge_id, user_id, challenge["email"]))
                _sign_in(user_id)
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                raise AccountError("That email address is already in use. Sign in or request a new code.", 409) from None
        return jsonify(user=_public(current_user()), csrfToken=session["csrf"])

    app.extensions["account_dummy_hash"] = generate_password_hash(secrets.token_urlsafe(24))
