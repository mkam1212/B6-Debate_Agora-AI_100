"""Round boundaries, closing turns, quota pauses and reviewer isolation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.gizmoapp_server import create_app
from server.gizmoapp_server.db import get_db
from server.gizmoapp_server.debate_rounds import round_context
from server.gizmoapp_server.debate_chat import select_participant_for_turn, build_reply_messages
from tests.auth_helpers import authenticated_client


def discussion(count=0):
    return {'question': 'Where should we draw the line on AI in creative work?',
            'participants': [{'id': f'role-{i}', 'name': name, 'identity': identity, 'prompt': 'Defend your priorities.',
                              'roleType': role_type, 'avatar': name[0]} for i, name, identity, role_type in
                             [(1, 'Maya', 'Working illustrator', 'Analyst'), (2, 'Elliot', 'Small publisher', 'Challenger'),
                              (3, 'Ruth', 'Independent artist', 'Builder')]],
            'messages': [{'kind': 'participant', 'roleId': f'role-{i % 3 + 1}', 'letter': 'A',
                          'body': f'Actual claim {i}.'} for i in range(count)],
            'pendingUserMessages': [], 'draft': '', 'debateStarted': True, 'paused': False}


class RoundTests(unittest.TestCase):
    def test_round_has_real_debate_then_three_distinct_closing_turns(self):
        self.assertEqual(round_context(discussion())['phase'], 'opening')
        for i in range(1, 6):
            self.assertEqual(round_context(discussion(i))['phase'], 'debate')
        for i in (6, 7, 8):
            context = round_context(discussion(i))
            self.assertEqual(context['phase'], 'closing')
            self.assertEqual(context['finalReply'], i == 8)
        with patch('server.gizmoapp_server.debate_chat.llm.chat', return_value=json.dumps({'participantId': 'role-3', 'focus': 'Answer Maya’s objection.'})) as model:
            choice = select_participant_for_turn(discussion(8))
        self.assertEqual(choice.participant['id'], 'role-3')
        payload = json.loads(model.call_args.args[0][1]['content'])
        self.assertEqual([p['participantId'] for p in payload['candidates']], ['role-3'])
        self.assertEqual(payload['round']['replyLimit'], 9)
        self.assertIn('strongest objection', payload['roundTask'])

    def test_opening_user_attachment_turn_and_mock_rows_do_not_skip_opening(self):
        saved = discussion()
        saved['messages'] = [{'kind': 'user', 'body': saved['question']},
                             {'kind': 'participant', 'body': 'Fake reply', 'origin': 'mock'}]
        self.assertEqual(round_context(saved)['phase'], 'opening')
        self.assertEqual(round_context(saved)['replyNumber'], 1)

    def test_quieter_roles_get_to_engage_and_same_speaker_cannot_take_consecutive_turns(self):
        saved = discussion(1)
        with patch('server.gizmoapp_server.debate_chat.llm.chat', return_value=json.dumps({'participantId': 'role-1', 'focus': 'Repeat my own point.'})) as model:
            choice = select_participant_for_turn(saved)
        self.assertNotEqual(choice.participant['id'], 'role-1')
        candidates = json.loads(model.call_args.args[0][1]['content'])['candidates']
        self.assertEqual({p['participantId'] for p in candidates}, {'role-2', 'role-3'})

    def test_new_user_input_and_completed_round_begin_fresh_bounded_rounds(self):
        saved = discussion(9)
        saved['messages'][-1]['roundEnd'] = True
        context = round_context(saved)
        self.assertEqual(context['replyNumber'], 1)
        self.assertTrue(context['continuedRound'])
        saved['messages'].append({'kind': 'user', 'body': 'What about consent?'})
        self.assertEqual(round_context(saved)['replyNumber'], 1)
        saved['messages'].append({'kind': 'participant', 'roleId': 'role-2', 'body': 'A new response.'})
        self.assertEqual(round_context(saved)['replyNumber'], 2)
        prompt = build_reply_messages(saved, saved['participants'][0])[0]['content']
        self.assertIn('central disagreement', prompt)
        self.assertIn('one or two short sentences', prompt)


class RoundHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({'TESTING': True, 'AUTO_MIGRATE': True, 'DB_PATH': Path(self.temp.name) / 'test.db',
                               'SECRET_KEY': 'round-test', 'URL_PREFIX': '', 'ACCOUNT_STARTER_REPLIES': 10,
                               'WEB_SEARCH_ENABLED': False}, shell_variant='text')
        self.client = authenticated_client(self.app)
        self.uid = self.client.get('/api/account/session').json['user']['id']
        saved = self.client.post('/api/debate/sessions', json=discussion(8)).json['session']
        self.path = '/api/debate/sessions/' + saved['id']
        self.saved = saved

    def test_json_and_stream_report_the_same_closing_boundary(self):
        with patch('server.gizmoapp_server.debate_api.generate_role_reply', return_value=('Maya, that changes my view on consent.', False, 'plain_text_v1')):
            reply = self.client.post(self.path + '/reply', json={'participantId': 'role-3'}).json
        self.assertTrue(reply['roundComplete'])
        self.assertEqual(reply['remaining'], 9)
        stream = [{'type': 'mode', 'replyMode': 'plain_text_v1'}, {'type': 'metadata', 'waitingForUser': False},
                  {'type': 'delta', 'text': 'Maya, I still disagree about consent.'}]
        second = self.client.post('/api/debate/sessions', json=discussion(8)).json['session']
        with patch('server.gizmoapp_server.debate_api.stream_role_reply', return_value=iter(stream)):
            response = self.client.post('/api/debate/sessions/' + second['id'] + '/reply', json={'participantId': 'role-3'}, headers={'Accept': 'text/event-stream'})
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertTrue(events[-1]['roundComplete'])
        self.assertEqual(events[-1]['remaining'], 8)
        snapshot = {**self.saved, 'paused': True, 'expectedRevision': self.saved['revision']}
        snapshot['messages'].append({'kind': 'participant', 'roleId': 'role-3', 'letter': 'A', 'body': reply['content'], 'roundEnd': True})
        self.assertEqual(self.client.put(self.path, json=snapshot).status_code, 200)
        restored = self.client.get(self.path).json['session']
        self.assertTrue(restored['messages'][-1]['roundEnd'])
        self.assertTrue(restored['paused'])

    def test_zero_balance_cannot_persist_an_unpaused_conversation(self):
        with self.app.app_context():
            get_db().execute('UPDATE account_wallets SET balance=0 WHERE user_id=?', (self.uid,))
            get_db().commit()
        response = self.client.put(self.path, json={**self.saved, 'paused': False, 'expectedRevision': self.saved['revision']})
        self.assertTrue(response.json['session']['paused'])
        self.assertEqual(self.client.post(self.path + '/reply', json={}).status_code, 402)

    def test_old_grant_does_not_make_another_email_a_reviewer(self):
        with self.app.app_context():
            get_db().execute('INSERT INTO account_admins VALUES (?)', (self.uid,))
            get_db().commit()
        self.assertFalse(self.client.get('/api/account/session').json['user']['isAdmin'])
        self.assertEqual(self.client.get('/api/account/credit-requests').status_code, 404)

    def test_sign_out_revokes_private_pages_and_session_api(self):
        csrf = self.client.get('/api/account/session').json['csrfToken']
        self.assertEqual(self.client.post('/api/account/sign-out', json={}, headers={'X-CSRF-Token': csrf}).status_code, 200)
        self.assertIsNone(self.client.get('/api/account/session').json['user'])
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertEqual(self.client.get('/api/debate/sessions').status_code, 401)
