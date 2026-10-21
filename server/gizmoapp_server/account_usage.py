"""Reply allowances and email-verified invitation rewards, backed by SQLite."""
import secrets
import time

from flask import current_app

from .db import WRITE_LOCK, get_db


def ensure_wallet(connection, user_id):
    wallet = connection.execute("SELECT * FROM account_wallets WHERE user_id=?", (user_id,)).fetchone()
    if wallet:
        return wallet
    allowance = current_app.config["ACCOUNT_STARTER_REPLIES"]
    connection.execute("INSERT INTO account_wallets (user_id,invite_code,balance) VALUES (?,?,?)", (user_id, secrets.token_urlsafe(12), allowance))
    connection.execute("INSERT INTO account_usage_entries VALUES (?,?,?,?,?,?)", (secrets.token_hex(16), user_id, "welcome", allowance, user_id, int(time.time())))
    return connection.execute("SELECT * FROM account_wallets WHERE user_id=?", (user_id,)).fetchone()


def award_invitation(connection, invitee_id, inviter_id):
    if not inviter_id or inviter_id == invitee_id:
        return
    if not connection.execute("SELECT id FROM account_users WHERE id=?", (inviter_id,)).fetchone():
        return
    reward = current_app.config["ACCOUNT_INVITE_REWARD"]
    now = int(time.time())
    inserted = connection.execute("INSERT OR IGNORE INTO account_referrals VALUES (?,?,?,?)", (invitee_id, inviter_id, reward, now)).rowcount
    if not inserted:
        return
    for user_id in (inviter_id, invitee_id):
        ensure_wallet(connection, user_id)
        connection.execute("UPDATE account_wallets SET balance=balance+? WHERE user_id=?", (reward, user_id))
        connection.execute("INSERT INTO account_usage_entries VALUES (?,?,?,?,?,?)", (secrets.token_hex(16), user_id, "invite", reward, invitee_id, now))
    connection.execute(
        "UPDATE account_email_invites SET state='accepted',invitee_id=?,updated_at=? "
        "WHERE inviter_id=? AND email=(SELECT email FROM account_users WHERE id=?)",
        (invitee_id, now, inviter_id, invitee_id),
    )


def usage_summary(user_id):
    connection = get_db()
    with WRITE_LOCK:
        connection.execute("BEGIN IMMEDIATE")
        ensure_wallet(connection, user_id)
        # Recover unused holds after an interrupted worker; active requests finish well before this.
        expired = connection.execute("SELECT id FROM account_reply_reservations WHERE user_id=? AND state='pending' AND created_at<?", (user_id, int(time.time()) - 900)).fetchall()
        for row in expired:
            connection.execute("UPDATE account_reply_reservations SET state='refunded' WHERE id=?", (row["id"],))
            connection.execute("UPDATE account_wallets SET balance=balance+1 WHERE user_id=?", (user_id,))
        wallet = connection.execute("SELECT * FROM account_wallets WHERE user_id=?", (user_id,)).fetchone()
        earned = connection.execute("SELECT COALESCE(SUM(delta),0) FROM account_usage_entries WHERE user_id=? AND kind='invite'", (user_id,)).fetchone()[0]
        invites = connection.execute("SELECT COUNT(*) FROM account_referrals WHERE inviter_id=?", (user_id,)).fetchone()[0]
        entries = connection.execute("SELECT kind,delta,created_at FROM account_usage_entries WHERE user_id=? ORDER BY created_at DESC, rowid DESC LIMIT 12", (user_id,)).fetchall()
        connection.commit()
    credit_request = connection.execute("SELECT state,created_at FROM account_credit_requests WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (user_id,)).fetchone()
    return {"remaining": wallet["balance"], "used": wallet["spent"], "earned": earned, "invites": invites, "rewardPerInvite": current_app.config["ACCOUNT_INVITE_REWARD"], "starterAllowance": current_app.config["ACCOUNT_STARTER_REPLIES"], "inviteCode": wallet["invite_code"], "creditRequest": dict(credit_request) if credit_request else None, "entries": [{"kind": row["kind"], "delta": row["delta"], "createdAt": row["created_at"]} for row in entries]}


def request_credit(user_id):
    from .account_api import AccountError
    connection = get_db()
    with WRITE_LOCK:
        connection.execute("BEGIN IMMEDIATE")
        pending = connection.execute("SELECT id FROM account_credit_requests WHERE user_id=? AND state='pending'", (user_id,)).fetchone()
        if pending:
            connection.commit()
            return
        wallet = ensure_wallet(connection, user_id)
        if wallet["balance"] > 0:
            raise AccountError("You still have replies available.", 409)
        recent = connection.execute("SELECT 1 FROM account_credit_requests WHERE user_id=? AND created_at>?", (user_id, int(time.time()) - 86400)).fetchone()
        if recent:
            raise AccountError("Your last request was reviewed. Please try again tomorrow.", 429)
        connection.execute("INSERT INTO account_credit_requests(id,user_id,created_at) VALUES (?,?,?)", (secrets.token_hex(16), user_id, int(time.time())))
        connection.commit()


def review_credit_request(request_id, replies=50, *, decline=False):
    """Apply one authorized review atomically, from the operator UI or server CLI."""
    if type(replies) is not int or not 1 <= replies <= 1000:
        raise ValueError("Choose between 1 and 1000 replies.")
    connection = get_db()
    with WRITE_LOCK:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM account_credit_requests WHERE id=?", (request_id,)).fetchone()
        if not row or row["state"] != "pending":
            connection.rollback()
            return False
        connection.execute("UPDATE account_credit_requests SET state=?,resolved_at=? WHERE id=?", ("declined" if decline else "approved", int(time.time()), request_id))
        if not decline:
            ensure_wallet(connection, row["user_id"])
            connection.execute("UPDATE account_wallets SET balance=balance+? WHERE user_id=?", (replies, row["user_id"]))
            connection.execute("INSERT INTO account_usage_entries VALUES (?,?,?,?,?,?)", (secrets.token_hex(16), row["user_id"], "credit_request", replies, request_id, int(time.time())))
        connection.commit()
        return True


def invitation_history(user_id, days, offset=0):
    # Show addresses only when this inviter supplied them; other referrals retain names only.
    cutoff = int(time.time()) - days * 86400 if days else 0
    rows = get_db().execute(
        "WITH history AS ("
        "SELECT u.name,u.avatar_color,r.reward,r.created_at AS joined_at,COALESCE(e.created_at,r.created_at) AS created_at,"
        "e.email,'accepted' AS state,r.invitee_id AS sort_id FROM account_referrals r "
        "JOIN account_users u ON u.id=r.invitee_id LEFT JOIN account_email_invites e "
        "ON e.inviter_id=r.inviter_id AND e.invitee_id=r.invitee_id WHERE r.inviter_id=? "
        "UNION ALL SELECT e.email,'#5d5fe9',0,NULL,e.created_at,e.email,e.state,e.id "
        "FROM account_email_invites e WHERE e.inviter_id=? AND e.state!='accepted') "
        "SELECT * FROM history WHERE created_at>=? ORDER BY created_at DESC,sort_id LIMIT 51 OFFSET ?",
        (user_id, user_id, cutoff, offset),
    ).fetchall()
    return {
        "invites": [{"name": row["name"], "avatarColor": row["avatar_color"],
                     "reward": row["reward"], "joinedAt": row["joined_at"],
                     **({"email": row["email"], "status": row["state"], "createdAt": row["created_at"]} if row["email"] else {})} for row in rows[:50]],
        "hasMore": len(rows) > 50,
        "nextOffset": offset + min(50, len(rows)),
    }


def reserve_reply(user_id, session_id=None):
    from .account_api import AccountError
    connection = get_db()
    reservation_id = secrets.token_hex(16)
    with WRITE_LOCK:
        connection.execute("BEGIN IMMEDIATE")
        ensure_wallet(connection, user_id)
        if session_id and connection.execute("SELECT 1 FROM account_reply_reservations WHERE session_id=? AND state='pending'", (session_id,)).fetchone():
            connection.rollback()
            raise AccountError("This debate is already running in another tab.", 409)
        changed = connection.execute("UPDATE account_wallets SET balance=balance-1 WHERE user_id=? AND balance>0", (user_id,)).rowcount
        if not changed:
            connection.rollback()
            raise AccountError("You’ve used all your AI replies. Open Usage to invite a friend or request more.", 402)
        connection.execute("INSERT INTO account_reply_reservations(id,user_id,state,created_at,session_id) VALUES (?,?, 'pending',?,?)", (reservation_id, user_id, int(time.time()), session_id))
        connection.commit()
    return reservation_id


def finish_reply(reservation_id, success, *, commit=True):
    if not reservation_id:
        return
    connection = get_db()
    with WRITE_LOCK:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM account_reply_reservations WHERE id=? AND state='pending'", (reservation_id,)).fetchone()
        if row:
            connection.execute("UPDATE account_reply_reservations SET state=? WHERE id=?", ("completed" if success else "refunded", reservation_id))
            if success:
                connection.execute("UPDATE account_wallets SET spent=spent+1 WHERE user_id=?", (row["user_id"],))
                connection.execute("INSERT INTO account_usage_entries VALUES (?,?,?,?,?,?)", (secrets.token_hex(16), row["user_id"], "reply", -1, reservation_id, int(time.time())))
            else:
                connection.execute("UPDATE account_wallets SET balance=balance+1 WHERE user_id=?", (row["user_id"],))
        if commit:
            connection.commit()
