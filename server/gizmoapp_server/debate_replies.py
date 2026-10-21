"""Commit a generated reply and its charge together, before acknowledging delivery."""
import json

from .account_api import AccountError
from .account_usage import finish_reply
from .db import WRITE_LOCK, get_db
from .debate_rounds import round_context
from .debate_sessions import _dump, _now, fetch_session


def merge_committed_replies(connection, session_id, messages):
    """A draft save captured during streaming must never erase a paid reply."""
    merged = list(messages)
    rows = connection.execute(
        "SELECT message_index,message_json FROM debate_reply_commits WHERE session_id=? ORDER BY revision",
        (session_id,),
    )
    for row in rows:
        message = json.loads(row['message_json'])
        index = next((i for i, item in enumerate(merged) if item.get('id') == message['id']), None)
        if index is not None:
            merged[index] = message
            continue
        index = min(row['message_index'], len(merged))
        # Older clients used a locally constructed copy without the server ID.
        if index < len(merged) and not merged[index].get('id') and all(
            merged[index].get(key) == message.get(key) for key in ('kind', 'roleId', 'body')
        ):
            merged[index] = message
        else:
            merged.insert(index, message)
    return merged


def only_reply_revisions(connection, session_id, expected, current):
    """Rebase only our generated appends; other tabs' edits still conflict."""
    if expected >= current:
        return expected == current
    count = connection.execute(
        "SELECT count(*) FROM debate_reply_commits WHERE session_id=? AND revision>? AND revision<=?",
        (session_id, expected, current),
    ).fetchone()[0]
    return count == current - expected


def commit_reply(session, participant, reservation_id, content, *, waiting=False, citations=None):
    from .debate_api import MAX_SESSION_MESSAGES, MAX_SESSION_MESSAGE_LENGTH, MAX_SESSION_SNAPSHOT_BYTES
    content = content.strip()
    if not content or len(content) > MAX_SESSION_MESSAGE_LENGTH:
        raise AccountError('The reply could not be saved. Please try again.', 503)
    connection = get_db()
    with WRITE_LOCK:
        connection.execute('BEGIN IMMEDIATE')
        try:
            hold = connection.execute(
                "SELECT * FROM account_reply_reservations WHERE id=? AND state='pending'", (reservation_id,),
            ).fetchone()
            saved = fetch_session(connection, session['id'])
            if not hold or not saved or saved['ownerId'] != hold['user_id']:
                raise AccountError('This conversation is unavailable.', 409)
            if saved['messages'] != session['messages'] or saved['question'] != session['question'] or not any(
                role == participant for role in saved['participants']
            ):
                raise AccountError('This conversation changed. Reload it before continuing.', 409)
            if len(saved['messages']) + len(saved['pendingUserMessages']) >= MAX_SESSION_MESSAGES:
                raise AccountError('This conversation is full. Start a new debate to keep going.', 409)
            final = round_context(session)['finalReply']
            index = session['participants'].index(participant)
            message = {'id': 'reply-' + reservation_id, 'kind': 'participant', 'roleId': participant['id'],
                       'letter': chr(65 + index), 'body': content}
            if waiting:
                message['waitingForUser'] = True
            if final:
                message['roundEnd'] = True
            if citations:
                message['citations'] = citations
            count = len(saved['messages'])
            saved['messages'].append(message)
            if len(_dump(saved).encode()) > MAX_SESSION_SNAPSHOT_BYTES:
                raise AccountError('This conversation is full. Start a new debate to keep going.', 409)
            remaining = connection.execute('SELECT balance FROM account_wallets WHERE user_id=?', (hold['user_id'],)).fetchone()[0]
            paused = saved['paused'] or remaining == 0 or final and not saved['pendingUserMessages']
            revision = saved['revision'] + 1
            connection.execute(
                'UPDATE debate_sessions SET messages_json=?,paused=?,revision=?,updated_at=? WHERE id=?',
                (_dump(saved['messages']), int(paused), revision, _now(), session['id']),
            )
            connection.execute('INSERT INTO debate_reply_commits VALUES (?,?,?,?,?)',
                               (reservation_id, session['id'], revision, count, _dump(message)))
            finish_reply(reservation_id, True, commit=False)
            connection.commit()
            return {'message': message, 'revision': revision, 'remaining': remaining, 'roundComplete': final}
        except Exception:
            connection.rollback()
            raise
