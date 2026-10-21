"""Real HTTP boundaries for private histories, read-only shares, outcomes and credit review."""
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from server.gizmoapp_server import create_app
from server.gizmoapp_server.db import get_db
from server.gizmoapp_server.debate_sessions import create_session, fetch_session
from server.gizmoapp_server.account_usage import reserve_reply, finish_reply, review_credit_request
from server.gizmoapp_server.account_api import AccountError
from tests.auth_helpers import authenticated_client


def snapshot():
    return {"question": "Should we launch the pilot?", "participants": [
        {"id": "role-1", "name": "Maya", "avatar": "M", "identity": "Researcher", "prompt": "Test assumptions.", "roleType": "Analyst"}],
        "messages": [{"kind": "participant", "roleId": "role-1", "letter": "M", "body": body} for body in
                     ["A small pilot can test demand.", "The cost still matters.", "We need a measurable test."]],
        "draft": "Private unfinished thought", "pendingUserMessages": [{"id": "msg-queued", "body": "Not sent yet"}],
        "draftAttachments": [], "debateStarted": True, "paused": True}


class ProductReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({"TESTING": True, "AUTO_MIGRATE": True, "DB_PATH": Path(self.temp.name) / "test.db",
                               "SECRET_KEY": "product-fixture", "URL_PREFIX": "", "ACCOUNT_STARTER_REPLIES": 10}, shell_variant="text")
        self.owner, self.other = authenticated_client(self.app), authenticated_client(self.app)
        self.guest = self.app.test_client()
        self.user_id = self.owner.get('/api/account/session').json['user']['id']
        self.other_id = self.other.get('/api/account/session').json['user']['id']
        self.saved = self.owner.post('/api/debate/sessions', json=snapshot()).json['session']
        self.path = '/api/debate/sessions/' + self.saved['id']

    def share(self):
        token = self.owner.post(self.path + '/sharing', json={}).json['token']
        return token, self.path + '?share=' + token

    def test_private_session_has_no_anonymous_or_other_account_access(self):
        self.assertEqual(self.saved['access'], 'owner')
        self.assertNotIn('ownerId', self.saved)
        for client in (self.guest, self.other):
            self.assertEqual(client.get(self.path).status_code, 404)
            self.assertEqual(client.put(self.path, json={}).status_code, 401 if client is self.guest else 404)
            for suffix in ('reply', 'summary', 'sharing', 'feedback'):
                self.assertIn(client.post(self.path + '/' + suffix, json={}).status_code, (401, 404))
        self.assertEqual(self.other.get('/api/debate/sessions').json['sessions'], [])

    def test_share_is_revocable_read_only_and_hides_unsent_material(self):
        token, url = self.share()
        result = self.guest.get(url)
        shared = result.json['session']
        self.assertEqual(shared['access'], 'viewer')
        self.assertEqual(shared['draft'], '')
        self.assertEqual(shared['pendingUserMessages'], [])
        self.assertEqual(shared['draftAttachments'], [])
        self.assertIn('no-store', result.headers['Cache-Control'])
        self.assertEqual(result.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(self.other.put(url, json={}).status_code, 404)
        new_token, new_url = self.share()
        self.assertNotEqual(token, new_token)
        self.assertEqual(self.guest.get(url).status_code, 404)
        self.assertEqual(self.guest.get(new_url).status_code, 200)
        self.owner.delete(self.path + '/sharing')
        self.assertEqual(self.guest.get(new_url).status_code, 404)

    def test_new_browser_same_account_loads_server_history(self):
        with self.app.app_context():
            from server.gizmoapp_server.account_api import _digest
            import secrets
            token = secrets.token_urlsafe(24)
            get_db().execute('INSERT INTO account_sessions VALUES (?,?,?)', (_digest(token), self.user_id, int(time.time()) + 3600))
            get_db().commit()
        second = self.app.test_client()
        with second.session_transaction() as sess:
            sess['account_token'] = token
        self.assertEqual(second.get('/api/debate/sessions').json['sessions'][0]['id'], self.saved['id'])
        self.assertEqual(second.get(self.path).status_code, 200)

    def test_history_searches_message_content_and_keeps_creation_order(self):
        new = self.owner.post('/api/debate/sessions', json={**snapshot(), 'question': 'A second discussion'}).json['session']
        before = [r['id'] for r in self.owner.get('/api/debate/sessions').json['sessions']]
        self.owner.put(self.path, json={**snapshot(), 'expectedRevision': self.saved['revision'], 'draft': 'Edited now'})
        after = [r['id'] for r in self.owner.get('/api/debate/sessions').json['sessions']]
        self.assertEqual(before, after)
        self.assertEqual(before[0], new['id'])
        found = self.owner.get('/api/debate/sessions?q=measurable').json['sessions']
        self.assertEqual(len(found), 2)

    def test_rename_does_not_rewrite_original_question_or_summary_facts(self):
        self.assertEqual(self.owner.patch(self.path + '/manage', json={'title': 'Launch decision'}).status_code, 200)
        saved = self.owner.get(self.path).json['session']
        self.assertEqual(saved['title'], 'Launch decision')
        self.assertEqual(saved['question'], snapshot()['question'])
        self.assertEqual(self.owner.get('/api/debate/sessions?q=Launch%20decision').json['sessions'][0]['question'], 'Launch decision')

    def test_archive_restore_delete_and_ownership(self):
        self.assertEqual(self.other.delete(self.path + '/manage').status_code, 404)
        self.owner.patch(self.path + '/manage', json={'archived': True})
        self.assertEqual(self.owner.get('/api/debate/sessions').json['sessions'], [])
        self.assertEqual(len(self.owner.get('/api/debate/sessions?archived=1').json['sessions']), 1)
        self.owner.patch(self.path + '/manage', json={'archived': False})
        _, link = self.share()
        self.owner.delete(self.path + '/manage')
        self.assertEqual(self.guest.get(link).status_code, 404)
        self.assertEqual(self.owner.get('/api/debate/sessions').json['sessions'], [])

    def test_legacy_import_is_private_idempotent_copy_without_claiming_original(self):
        with self.app.app_context():
            original = create_session(get_db(), snapshot())
            get_db().execute('INSERT INTO debate_web_sources VALUES (?,?,?,?,?,?,?)',
                             (original['id'], 'web-' + 'a' * 20, 'https://example.org/evidence', 'Evidence', 'Unverified response excerpt', 'scope', int(time.time())))
            get_db().commit()
        url = '/api/debate/sessions/' + original['id']
        self.assertEqual(self.guest.get(url).json['session']['access'], 'legacy')
        self.assertEqual(self.owner.put(url, json={}).status_code, 404)
        imported = self.owner.post('/api/debate/sessions/import', json={'sessionIds': [original['id']]}).json['imported']
        again = self.owner.post('/api/debate/sessions/import', json={'sessionIds': [original['id']]}).json['imported']
        self.assertEqual(imported, again)
        new_id = imported[original['id']]
        self.assertNotEqual(new_id, original['id'])
        self.assertEqual(self.guest.get('/api/debate/sessions/' + new_id).status_code, 404)
        with self.app.app_context():
            self.assertIsNone(fetch_session(get_db(), original['id'])['ownerId'])
            self.assertEqual(fetch_session(get_db(), new_id)['ownerId'], self.user_id)
            self.assertEqual(get_db().execute('SELECT count(*) FROM debate_web_sources WHERE session_id=?', (new_id,)).fetchone()[0], 1)
        # Existing account-owned conversations cannot be imported by a different account.
        self.assertEqual(self.other.post('/api/debate/sessions/import', json={'sessionIds': [new_id]}).json['imported'], {})

    def test_imported_copy_can_retire_original_link_without_losing_data(self):
        with self.app.app_context():
            original = create_session(get_db(), snapshot())
        result = self.owner.post('/api/debate/sessions/import', json={'sessionIds': [original['id']]}).json
        copy_id = result['imported'][original['id']]
        path = '/api/debate/sessions/' + copy_id
        self.assertTrue(self.owner.get(path).json['session']['legacyLinkActive'])
        self.assertEqual(self.owner.patch(path + '/manage', json={'retireLegacyLink': True}).status_code, 200)
        self.assertEqual(self.guest.get('/api/debate/sessions/' + original['id']).status_code, 404)
        self.assertEqual(self.owner.get(path).status_code, 200)
        self.assertFalse(self.owner.get(path).json['session']['legacyLinkActive'])
        self.assertEqual(self.other.post('/api/debate/sessions/import', json={'sessionIds': [original['id']]}).json['imported'], {})
        self.assertEqual(self.owner.post('/api/debate/sessions/import', json={'sessionIds': [original['id']]}).json['imported'][original['id']], copy_id)

    def upload(self, name='notes.txt'):
        csrf = self.owner.get('/api/account/session').json['csrfToken']
        return self.owner.post('/api/debate/attachments', data={'file': (BytesIO(b'Private evidence'), name)},
                               headers={'X-CSRF-Token': csrf}).json['attachment']

    def test_uploads_require_ownership_and_shared_drafts_stay_private(self):
        attachment = self.upload()
        private_draft = self.upload('draft.txt')
        payload = snapshot()
        payload['messages'].append({'kind': 'user', 'body': 'A supporting file', 'attachments': [attachment]})
        payload['draftAttachments'] = [private_draft]
        payload['expectedRevision'] = self.saved['revision']
        self.assertEqual(self.owner.put(self.path, json=payload).status_code, 200)
        token, _ = self.share()
        file_path = '/api/debate/attachments/' + attachment['id'] + '/preview'
        self.assertEqual(self.other.get(file_path).status_code, 404)
        self.assertEqual(self.owner.get(file_path).status_code, 200)
        shared_query = '?session=' + self.saved['id'] + '&share=' + token
        self.assertEqual(self.guest.get(file_path + shared_query).status_code, 200)
        self.assertEqual(self.guest.get('/api/debate/attachments/' + private_draft['id'] + '/preview' + shared_query).status_code, 404)
        stolen = self.other.post('/api/debate/sessions', json={**snapshot(), 'draftAttachments': [attachment]})
        self.assertEqual(stolen.status_code, 400)
        self.owner.delete(self.path + '/sharing')
        self.assertEqual(self.guest.get(file_path + shared_query).status_code, 404)
        self.owner.delete(self.path + '/manage')
        self.assertEqual(self.owner.get(file_path).status_code, 404)

    def test_summary_persists_reuses_cache_and_marks_new_content_stale(self):
        body = 'What changed\nA smaller test is now an option.\n\nEvidence & unknowns\nDemand is still unknown.'
        with patch('server.gizmoapp_server.debate_api.llm.chat', return_value=body) as model:
            self.assertEqual(self.owner.post(self.path + '/summary', json={}).json['summary'], body)
            self.owner.post(self.path + '/summary', json={})
            model.assert_called_once()
        saved = self.owner.get(self.path).json['session']
        self.assertFalse(saved['summary']['isStale'])
        self.owner.put(self.path, json={**snapshot(), 'draft': 'Another draft', 'expectedRevision': saved['revision']})
        saved = self.owner.get(self.path).json['session']
        self.assertFalse(saved['summary']['isStale'])
        payload = snapshot()
        payload['messages'].append({'kind': 'participant', 'roleId': 'role-1', 'letter': 'M', 'body': 'A new claim.'})
        self.owner.put(self.path, json={**payload, 'expectedRevision': saved['revision']})
        saved = self.owner.get(self.path).json['session']
        self.assertTrue(saved['summary']['isStale'])
        _, url = self.share()
        self.assertEqual(self.guest.get(url).json['session']['summary']['summary'], body)

    def test_feedback_is_account_scoped_and_does_not_duplicate(self):
        self.assertEqual(self.other.post(self.path + '/feedback', json={'helpful': True}).status_code, 404)
        for helpful in (True, False):
            self.assertEqual(self.owner.post(self.path + '/feedback', json={'helpful': helpful}).status_code, 200)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT count(*) FROM debate_feedback').fetchone()[0], 1)
            self.assertEqual(get_db().execute('SELECT helpful FROM debate_feedback').fetchone()[0], 0)

    def test_duplicate_running_session_reserves_only_one_credit(self):
        with self.app.app_context():
            reservation = reserve_reply(self.user_id, self.saved['id'])
            with self.assertRaises(AccountError):
                reserve_reply(self.user_id, self.saved['id'])
            self.assertEqual(get_db().execute('SELECT balance FROM account_wallets WHERE user_id=?', (self.user_id,)).fetchone()[0], 9)
            finish_reply(reservation, False)
            self.assertEqual(get_db().execute('SELECT balance FROM account_wallets WHERE user_id=?', (self.user_id,)).fetchone()[0], 10)

    def test_credit_request_requires_csrf_is_idempotent_and_does_not_grant_automatically(self):
        with self.app.app_context():
            get_db().execute('UPDATE account_wallets SET balance=0 WHERE user_id=?', (self.user_id,)); get_db().commit()
        self.assertEqual(self.owner.post('/api/account/credit-request', json={}).status_code, 403)
        csrf = self.owner.get('/api/account/session').json['csrfToken']
        for _ in range(2):
            response = self.owner.post('/api/account/credit-request', json={}, headers={'X-CSRF-Token': csrf})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['usage']['remaining'], 0)
            self.assertEqual(response.json['usage']['creditRequest']['state'], 'pending')
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT count(*) FROM account_credit_requests').fetchone()[0], 1)
            request_id = get_db().execute('SELECT id FROM account_credit_requests').fetchone()[0]
            self.assertTrue(review_credit_request(request_id, 10))
            self.assertFalse(review_credit_request(request_id, 10))
            self.assertEqual(get_db().execute('SELECT balance FROM account_wallets WHERE user_id=?', (self.user_id,)).fetchone()[0], 10)

    def test_review_is_admin_only_and_repeated_submission_cannot_double_grant(self):
        self.assertEqual(self.owner.get('/api/account/credit-requests').status_code, 404)
        with self.app.app_context():
            get_db().execute('UPDATE account_users SET email=? WHERE id=?', ('guolinn@student.ubc.ca', self.other_id))
            get_db().execute('INSERT INTO account_admins VALUES (?)', (self.other_id,))
            get_db().execute('INSERT INTO account_credit_requests(id,user_id,created_at) VALUES (?,?,?)', ('a' * 32, self.user_id, int(time.time())))
            get_db().commit()
        csrf = self.other.get('/api/account/session').json['csrfToken']
        self.assertEqual(len(self.other.get('/api/account/credit-requests').json['requests']), 1)
        for expected in (True, False):
            response = self.other.post('/api/account/credit-requests', json={'id': 'a' * 32, 'replies': 10}, headers={'X-CSRF-Token': csrf})
            self.assertEqual(response.json['ok'], expected)
        self.assertEqual(self.owner.get('/api/account/usage').json['usage']['remaining'], 20)

    def test_reviewer_can_choose_custom_amount_with_validation_and_exactly_once_credit(self):
        with self.app.app_context():
            connection = get_db()
            connection.execute('UPDATE account_users SET email=? WHERE id=?', ('guolinn@student.ubc.ca', self.other_id))
            connection.execute('INSERT INTO account_admins VALUES (?)', (self.other_id,))
            connection.execute('UPDATE account_wallets SET balance=0 WHERE user_id=?', (self.user_id,))
            connection.commit()
        csrf_owner = self.owner.get('/api/account/session').json['csrfToken']
        self.assertEqual(self.owner.post('/api/account/credit-request', json={}, headers={'X-CSRF-Token': csrf_owner}).status_code, 200)
        csrf = self.other.get('/api/account/session').json['csrfToken']
        request_id = self.other.get('/api/account/credit-requests').json['requests'][0]['id']
        self.assertEqual(self.owner.post('/api/account/credit-requests', json={'id': request_id, 'replies': 137}, headers={'X-CSRF-Token': csrf_owner}).status_code, 404)
        for amount in (0, -1, 1001, 1.5, True, '100'):
            response = self.other.post('/api/account/credit-requests', json={'id': request_id, 'replies': amount}, headers={'X-CSRF-Token': csrf})
            self.assertEqual(response.status_code, 400, amount)
        for expected in (True, False):
            response = self.other.post('/api/account/credit-requests', json={'id': request_id, 'replies': 137}, headers={'X-CSRF-Token': csrf})
            self.assertEqual(response.json['ok'], expected)
        self.assertEqual(self.owner.get('/api/account/usage').json['usage']['remaining'], 137)
        with self.app.app_context():
            rows = get_db().execute("SELECT delta FROM account_usage_entries WHERE user_id=? AND kind='credit_request'", (self.user_id,)).fetchall()
            self.assertEqual([row['delta'] for row in rows], [137])

    def test_previous_discussion_api_does_not_expose_other_users(self):
        payload = {'question': 'Which pilot should we run?', 'title': 'Pilot', 'options': ['A', 'B']}
        created = self.owner.post('/api/debate/discussions', json=payload)
        self.assertEqual(created.status_code, 201)
        path = '/api/debate/discussions/' + created.json['discussion']['id']
        self.assertEqual(self.other.get(path).status_code, 404)
        self.assertEqual(self.guest.get(path).status_code, 401)
        self.assertEqual(self.other.get('/api/debate/discussions').json['discussions'], [])
        self.assertEqual(self.owner.get(path).status_code, 200)


if __name__ == '__main__': unittest.main()
