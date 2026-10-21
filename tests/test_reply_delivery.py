"""Delivery failures must not lose a paid reply or charge an unsaved reply."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.gizmoapp_server import create_app
from server.gizmoapp_server.db import get_db
from tests.auth_helpers import authenticated_client
from tests.test_debate import chat_session_payload, question_payload


class ReplyDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({'TESTING': True, 'AUTO_MIGRATE': True, 'DB_PATH': Path(self.temp.name) / 'audit.db',
                               'URL_PREFIX': '', 'SECRET_KEY': 'delivery-fixture', 'ACCOUNT_STARTER_REPLIES': 2}, shell_variant='text')
        self.client = authenticated_client(self.app)
        self.uid = self.client.get('/api/account/session').json['user']['id']
        payload = chat_session_payload()
        payload['paused'] = False
        self.saved = self.client.post('/api/debate/sessions', json=payload).json['session']
        self.path = '/api/debate/sessions/' + self.saved['id']

    def stream(self, *args, fail=False, **kwargs):
        yield {'type': 'mode', 'replyMode': 'plain_text_stream_v1'}
        yield {'type': 'metadata', 'waitingForUser': False}
        yield {'type': 'delta', 'text': 'Jordan, that still leaves the beginner without an instructor.'}
        if fail:
            from server.gizmoapp_server.llm import CourseLLMError
            raise CourseLLMError('connection failed')

    def complete(self):
        with patch('server.gizmoapp_server.debate_api.stream_role_reply', side_effect=self.stream):
            response = self.client.post(self.path + '/reply', json={'participantId': 'role-1'}, headers={'Accept': 'text/event-stream'})
            events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith('data: ')]
        self.assertEqual(events[-1]['type'], 'done', events)
        return events[-1]

    def usage(self):
        return self.client.get('/api/account/session').json['usage']

    def test_stream_commit_is_durable_without_any_client_save_or_acknowledgement(self):
        done = self.complete()
        saved = self.client.get(self.path).json['session']
        self.assertEqual(saved['messages'][-1], done['message'])
        self.assertEqual(saved['revision'], self.saved['revision'] + 1)
        self.assertEqual(self.usage()['remaining'], 1)
        self.assertEqual(self.usage()['used'], 1)

    def test_closing_browser_midstream_refunds_and_publishes_no_partial_reply(self):
        with patch('server.gizmoapp_server.debate_api.stream_role_reply', side_effect=self.stream):
            response = self.client.post(self.path + '/reply', json={'participantId': 'role-1'}, headers={'Accept': 'text/event-stream'}, buffered=False)
            next(response.response)  # start
            next(response.response)  # delta
            response.close()
        self.assertEqual(self.usage()['remaining'], 2)
        self.assertEqual(self.usage()['used'], 0)
        self.assertEqual(self.client.get(self.path).json['session']['messages'], self.saved['messages'])

    def test_partial_model_failure_refunds_and_does_not_publish(self):
        with patch('server.gizmoapp_server.debate_api.stream_role_reply', side_effect=lambda *a, **kw: self.stream(fail=True)):
            response = self.client.post(self.path + '/reply', json={'participantId': 'role-1'}, headers={'Accept': 'text/event-stream'})
            self.assertIn('"type": "error"', response.get_data(as_text=True))
        self.assertEqual(self.usage()['remaining'], 2)
        self.assertEqual(self.client.get(self.path).json['session']['messages'], self.saved['messages'])

    def test_ledger_failure_rolls_back_reply_and_refunds(self):
        original_finish = __import__('server.gizmoapp_server.account_usage', fromlist=['finish_reply']).finish_reply

        def fail_charge(reservation, success, **kwargs):
            if success:
                raise sqlite3.OperationalError('simulated storage failure')
            return original_finish(reservation, success, **kwargs)

        with patch('server.gizmoapp_server.debate_replies.finish_reply', side_effect=fail_charge), patch(
            'server.gizmoapp_server.debate_api.generate_role_reply', return_value=('A complete reply.', False, 'plain_text_v1')
        ):
            response = self.client.post(self.path + '/reply', json={'participantId': 'role-1'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.usage()['remaining'], 2)
        self.assertEqual(self.usage()['used'], 0)
        self.assertEqual(self.client.get(self.path).json['session']['messages'], self.saved['messages'])

    def test_draft_save_captured_during_stream_rebases_without_erasing_reply(self):
        done = self.complete()
        payload = {**self.saved, 'expectedRevision': self.saved['revision'], 'draft': 'My new unsent thought',
                   'pendingUserMessages': [{'id': 'queued-after-stream', 'body': 'What if the instructor leaves?'}]}
        response = self.client.put(self.path, json=payload)
        self.assertEqual(response.status_code, 200, response.json)
        saved = response.json['session']
        self.assertEqual(saved['messages'][-1], done['message'])
        self.assertEqual(saved['draft'], 'My new unsent thought')
        self.assertEqual(len(saved['pendingUserMessages']), 1)
        self.assertEqual(self.usage()['used'], 1)

    def test_another_tabs_edit_is_still_a_conflict(self):
        self.complete()
        current = self.client.get(self.path).json['session']
        edit = self.client.put(self.path, json={**current, 'expectedRevision': current['revision'], 'draft': 'Other tab'})
        self.assertEqual(edit.status_code, 200)
        stale = self.client.put(self.path, json={**self.saved, 'expectedRevision': self.saved['revision'], 'draft': 'Old tab'})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get(self.path).json['session']['draft'], 'Other tab')

    def test_late_snapshot_with_new_revision_cannot_erase_paid_reply(self):
        done = self.complete()
        response = self.client.put(self.path, json={**self.saved, 'expectedRevision': done['revision']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['session']['messages'][-1], done['message'])

    def test_json_delivery_and_legacy_client_save_store_reply_exactly_once(self):
        with patch('server.gizmoapp_server.debate_api.generate_role_reply', return_value=('A complete reply.', False, 'plain_text_v1')):
            result = self.client.post(self.path + '/reply', json={'participantId': 'role-1'}).json
        old_copy = {'kind': 'participant', 'roleId': 'role-1', 'letter': 'A', 'body': result['content']}
        saved = self.client.put(self.path, json={**self.saved, 'expectedRevision': self.saved['revision'],
                                               'messages': self.saved['messages'] + [old_copy]}).json['session']
        self.assertEqual(sum(m['body'] == result['content'] for m in saved['messages']), 1)
        self.assertEqual(saved['messages'][-1]['id'], result['message']['id'])

    def test_no_credit_cannot_call_either_live_or_retired_generation(self):
        with self.app.app_context():
            get_db().execute('UPDATE account_wallets SET balance=0 WHERE user_id=?', (self.uid,))
            get_db().commit()
        old = self.client.post('/api/debate/discussions', json=question_payload()).json['discussion']
        with patch('server.gizmoapp_server.debate_api.start_run') as old_run, patch('server.gizmoapp_server.debate_api.select_participant_for_turn') as coordinator:
            self.assertEqual(self.client.post(self.path + '/reply', json={}).status_code, 402)
            for route in ('run', 'continue'):
                self.assertEqual(self.client.post('/api/debate/discussions/' + old['id'] + '/' + route, json={'useAssumptions': True}).status_code, 410)
            old_run.assert_not_called()
            coordinator.assert_not_called()
        self.assertEqual(self.client.get('/api/debate/discussions/' + old['id']).status_code, 200)

    def test_second_running_request_does_not_call_the_coordinator(self):
        from server.gizmoapp_server.account_usage import reserve_reply, finish_reply
        with self.app.app_context():
            hold = reserve_reply(self.uid, self.saved['id'])
        with patch('server.gizmoapp_server.debate_api.select_participant_for_turn') as coordinator:
            self.assertEqual(self.client.post(self.path + '/reply', json={}).status_code, 409)
            coordinator.assert_not_called()
        with self.app.app_context():
            finish_reply(hold, False)
        self.assertEqual(self.usage()['remaining'], 2)

    def test_message_sent_and_pause_saved_during_stream_survive_completion(self):
        from server.gizmoapp_server.debate_sessions import fetch_session, update_session

        def model(*args, **kwargs):
            yield from self.stream()
            with self.app.app_context():
                db = get_db()
                saved = fetch_session(db, self.saved['id'])
                snapshot = {**saved, 'draft': 'A newer thought', 'paused': True,
                            'pendingUserMessages': [{'id': 'queued-1', 'body': 'What about cost?'}]}
                update_session(db, saved['id'], snapshot, saved['revision'])

        with patch('server.gizmoapp_server.debate_api.stream_role_reply', side_effect=model):
            response = self.client.post(self.path + '/reply', json={'participantId': 'role-1'}, headers={'Accept': 'text/event-stream'})
            response.get_data()
        saved = self.client.get(self.path).json['session']
        self.assertEqual(saved['draft'], 'A newer thought')
        self.assertTrue(saved['paused'])
        self.assertEqual(saved['pendingUserMessages'][0]['body'], 'What about cost?')
        self.assertEqual(len(saved['messages']), len(self.saved['messages']) + 1)
        self.assertEqual(self.usage()['used'], 1)

    def test_zero_allowance_pause_is_committed_with_last_reply(self):
        with self.app.app_context():
            get_db().execute('UPDATE account_wallets SET balance=1 WHERE user_id=?', (self.uid,))
            get_db().commit()
        self.complete()
        self.assertTrue(self.client.get(self.path).json['session']['paused'])
        self.assertEqual(self.usage()['remaining'], 0)

    def test_full_conversation_cannot_trigger_free_failed_generation(self):
        payload = chat_session_payload()
        payload['messages'] = [{'kind': 'user', 'body': 'A saved turn.'} for _ in range(100)]
        saved = self.client.post('/api/debate/sessions', json=payload).json['session']
        with patch('server.gizmoapp_server.debate_api.select_participant_for_turn') as coordinator, patch(
            'server.gizmoapp_server.debate_api.generate_role_reply'
        ) as model:
            response = self.client.post('/api/debate/sessions/' + saved['id'] + '/reply', json={})
        self.assertEqual(response.status_code, 409)
        coordinator.assert_not_called()
        model.assert_not_called()
        self.assertEqual(self.usage()['remaining'], 2)

    def test_closing_pause_survives_a_draft_save_captured_before_done(self):
        from tests.test_debate_rounds import discussion
        self.saved = self.client.post('/api/debate/sessions', json=discussion(8)).json['session']
        self.path = '/api/debate/sessions/' + self.saved['id']
        done = self.complete()
        self.assertTrue(done['roundComplete'])
        saved = self.client.put(self.path, json={**self.saved, 'expectedRevision': self.saved['revision'], 'draft': 'Still typing'}).json['session']
        self.assertTrue(saved['paused'])
        self.assertTrue(saved['messages'][-1]['roundEnd'])
        self.assertEqual(saved['draft'], 'Still typing')
