"""Private email invitations with a durable, idempotent delivery record."""
import ipaddress
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from flask import current_app, request

from .account_usage import ensure_wallet
from .account_email_design import invitation_email_content
from .config import scoped_path
from .db import WRITE_LOCK, get_db


def _invitation_url(code):
    from .account_api import AccountError
    origin = (current_app.config.get("PUBLIC_APP_URL") or request.url_root).rstrip("/")
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname or ""
        try:
            local = not ipaddress.ip_address(host).is_global
        except ValueError:
            local = host == "localhost" or host.endswith((".localhost", ".local")) or "." not in host
        valid = parsed.scheme in ("http", "https") and host and not local
        valid = valid and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
        valid = valid and parsed.path in ("", "/")
        parsed.port  # Reject malformed ports as well.
    except ValueError:
        valid = False
    if not valid:
        raise AccountError("Set GIZMOAPP_PUBLIC_URL to your public website before sending invitations.", 503)
    return origin + scoped_path(current_app.config["URL_PREFIX"], "/") + "?" + urlencode({"invite": code})


def _deliver(invite, api_key):
    # The stored payload and key are unchanged on retry, including after a timeout.
    mail = Request("https://api.resend.com/emails", data=invite["payload_json"].encode(), method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
        "User-Agent": "DebateAgora/1.0", "Idempotency-Key": "invite-" + invite["id"],
    })
    try:
        with urlopen(mail, timeout=10) as response:
            return response.status in (200, 201, 202)
    except (HTTPError, URLError, TimeoutError, OSError):
        # Never surface provider responses, credentials or recipient details.
        return False


def send_email_invitations(user, data):
    from .account_api import AccountError, _email
    raw = data.get("emails")
    if isinstance(raw, str) and len(raw) <= 1500:
        raw = raw.split(",")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 5:
        raise AccountError("Enter up to 5 email addresses, separated by commas.")
    emails = list(dict.fromkeys(_email({"email": email}) for email in raw))
    if user["email"] in emails:
        raise AccountError("Enter a friend’s email address.")
    api_key = current_app.config.get("RESEND_API_KEY")
    sender = current_app.config.get("RESEND_FROM_EMAIL")
    if not api_key or not sender:
        raise AccountError("Invitation emails are not configured yet.", 503)
    connection = get_db()
    now = int(time.time())
    jobs, already, registered = [], [], []
    with WRITE_LOCK:
        connection.execute("BEGIN IMMEDIATE")
        wallet = ensure_wallet(connection, user["id"])
        url = _invitation_url(wallet["invite_code"])
        reward = current_app.config["ACCOUNT_INVITE_REWARD"]
        for email in emails:
            if connection.execute('SELECT 1 FROM account_users WHERE email=?', (email,)).fetchone():
                registered.append(email)
                continue
            row = connection.execute("SELECT * FROM account_email_invites WHERE inviter_id=? AND email=?", (user["id"], email)).fetchone()
            if row and (row["state"] in ("pending", "accepted") or row["state"] == "sending" and row["updated_at"] > now - 60):
                already.append(email)
                continue
            if not row:
                message = {
                    "from": sender, "to": [email], **invitation_email_content(user["name"], url, reward),
                }
                invite_id = secrets.token_hex(16)
                connection.execute("INSERT INTO account_email_invites (id,inviter_id,email,payload_json,state,created_at,updated_at) VALUES (?,?,?,?, 'sending',?,?)", (invite_id, user["id"], email, json.dumps(message), now, now))
                row = connection.execute("SELECT * FROM account_email_invites WHERE id=?", (invite_id,)).fetchone()
            else:
                connection.execute("UPDATE account_email_invites SET state='sending',updated_at=? WHERE id=?", (now, row["id"]))
            jobs.append(dict(row))
        connection.commit()
    # At most five independent HTTP requests; no database or Flask context in workers.
    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(lambda job: _deliver(job, api_key), jobs))
    sent, failed = [], []
    with WRITE_LOCK:
        for job, success in zip(jobs, outcomes):
            (sent if success else failed).append(job["email"])
            connection.execute("UPDATE account_email_invites SET state=?,updated_at=? WHERE id=? AND state='sending'", ("pending" if success else "failed", int(time.time()), job["id"]))
        connection.commit()
    return {"sent": sent, "alreadyInvited": already, "alreadyRegistered": registered, "failed": failed}
