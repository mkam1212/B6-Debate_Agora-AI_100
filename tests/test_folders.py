"""Folder boundaries and filing that does not disturb live conversations."""
import tempfile
import unittest
from pathlib import Path

from server.gizmoapp_server import create_app
from server.gizmoapp_server.db import get_db, SCHEMA_MIGRATIONS
from tests.auth_helpers import authenticated_client
from tests.test_product_readiness import snapshot


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({"TESTING": True, "AUTO_MIGRATE": True,
                               "DB_PATH": Path(self.temp.name) / "folders.db", "SECRET_KEY": "folders-test",
                               "URL_PREFIX": ""}, shell_variant="text")
        self.owner, self.other = authenticated_client(self.app), authenticated_client(self.app)
        self.guest = self.app.test_client()
        self.folder = self.owner.post('/api/debate/folders', json={'name': 'Campus project'}).json['folder']

    def create(self, **extra):
        result = self.owner.post('/api/debate/sessions', json={**snapshot(), **extra})
        self.assertEqual(result.status_code, 201, result.json)
        return result.json['session']

    def test_folder_crud_and_session_filing_are_account_scoped(self):
        path = '/api/debate/folders/' + self.folder['id']
        saved = self.create()
        move = '/api/debate/sessions/' + saved['id'] + '/folder'
        self.assertEqual(self.other.get('/api/debate/folders').json['folders'], [])
        self.assertEqual(self.guest.get('/api/debate/folders').status_code, 401)
        for client in (self.other, self.guest):
            expected = 404 if client is self.other else 401
            self.assertEqual(client.patch(path, json={'name': 'Changed'}).status_code, expected)
            self.assertEqual(client.delete(path).status_code, expected)
            self.assertEqual(client.patch(move, json={'folderId': self.folder['id']}).status_code, expected)
        self.assertEqual(self.other.post('/api/debate/sessions', json={**snapshot(), 'folderId': self.folder['id']}).status_code, 404)
        self.assertEqual(self.owner.patch(path, json={'name': '  New name  '}).status_code, 200)
        self.assertEqual(self.owner.get('/api/debate/folders').json['folders'][0]['name'], 'New name')

    def test_move_does_not_change_revision_drafts_or_invalidate_saved_snapshot(self):
        saved = self.create()
        path = '/api/debate/sessions/' + saved['id']
        self.assertEqual(self.owner.patch(path + '/folder', json={'folderId': self.folder['id']}).status_code, 200)
        moved = self.owner.get(path).json['session']
        for field in ('revision', 'updatedAt', 'messages', 'draft', 'pendingUserMessages', 'paused'):
            self.assertEqual(moved[field], saved[field])
        result = self.owner.put(path, json={**snapshot(), 'expectedRevision': saved['revision'], 'draft': 'Typed during the move'})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json['session']['folderId'], self.folder['id'])
        self.assertEqual(result.json['session']['draft'], 'Typed during the move')
        self.assertEqual(self.owner.patch(path + '/folder', json={'folderId': None}).status_code, 200)
        self.assertIsNone(self.owner.get(path).json['session']['folderId'])

    def test_remove_folder_keeps_archived_conversations_and_sharing_without_exposing_folder(self):
        first = self.create(folderId=self.folder['id'])
        archived = self.create(folderId=self.folder['id'])
        path = '/api/debate/sessions/' + first['id']
        other_path = '/api/debate/sessions/' + archived['id']
        self.owner.patch(other_path + '/manage', json={'archived': True})
        token = self.owner.post(path + '/sharing', json={}).json['token']
        shared = self.guest.get(path + '?share=' + token).json['session']
        self.assertNotIn('folderId', shared)
        self.assertEqual(self.other.patch(path + '/folder?share=' + token, json={'folderId': None}).status_code, 404)
        self.assertEqual(self.owner.get('/api/debate/folders').json['folders'][0]['count'], 1)
        self.assertEqual(self.owner.delete('/api/debate/folders/' + self.folder['id']).status_code, 200)
        for conversation in (path, other_path):
            self.assertIsNone(self.owner.get(conversation).json['session']['folderId'])
        self.assertEqual(self.guest.get(path + '?share=' + token).status_code, 200)
        self.assertEqual(len(self.owner.get('/api/debate/sessions?archived=1').json['sessions']), 1)

    def test_folder_pages_have_independent_stable_pagination_and_global_search(self):
        for index in range(53):
            self.create(folderId=self.folder['id'], question=f'Campus question {index}')
        ungrouped = self.create(question='A separate conversation')
        path = '/api/debate/sessions?folder=' + self.folder['id']
        first = self.owner.get(path).json
        second = self.owner.get(path + '&offset=50').json
        self.assertEqual((len(first['sessions']), first['hasMore'], first['nextOffset']), (50, True, 50))
        self.assertEqual((len(second['sessions']), second['hasMore']), (3, False))
        ids = [item['id'] for item in first['sessions'] + second['sessions']]
        self.assertEqual(len(set(ids)), 53)
        self.assertEqual([item['id'] for item in self.owner.get('/api/debate/sessions?folder=none').json['sessions']], [ungrouped['id']])
        self.assertEqual(len(self.owner.get('/api/debate/sessions?q=Campus').json['sessions']), 50)
        self.assertEqual(self.other.get(path).status_code, 404)
        self.assertEqual(self.owner.get('/api/debate/folders').json['folders'][0]['count'], 53)
        self.owner.patch('/api/debate/sessions/' + ids[0] + '/folder', json={'folderId': None})
        after = self.owner.get(path).json['sessions']
        self.assertEqual([item['id'] for item in after[:49]], ids[1:50])

    def test_invalid_input_never_files_into_missing_or_foreign_folder(self):
        saved = self.create()
        path = '/api/debate/sessions/' + saved['id'] + '/folder'
        for value in ('', [], {}, 123, 'not-a-folder', 'a' * 32):
            self.assertEqual(self.owner.patch(path, json={'folderId': value}).status_code, 404)
            self.assertEqual(self.owner.post('/api/debate/sessions', json={**snapshot(), 'folderId': value}).status_code, 404)
        self.assertEqual(self.owner.patch(path, json={}).status_code, 400)
        for name in ('', '  ', 'a' * 49, None, 'line\nbreak'):
            self.assertEqual(self.owner.post('/api/debate/folders', json={'name': name}).status_code, 400)
        result = self.owner.post('/api/debate/folders', json={'name': '中文、emoji 🌤️'})
        self.assertEqual(result.status_code, 201)
        self.assertIsNone(self.owner.get('/api/debate/sessions/' + saved['id']).json['session']['folderId'])

    def test_folder_and_ungrouped_order_follow_conversation_creation_not_filing_or_replies(self):
        second = self.owner.post('/api/debate/folders', json={'name': 'Second'}).json['folder']
        empty = self.owner.post('/api/debate/folders', json={'name': 'Empty'}).json['folder']
        newest = self.create(folderId=self.folder['id'])
        oldest = self.create(folderId=self.folder['id'])
        middle = self.create(folderId=second['id'])
        ungrouped = self.create()
        with self.app.app_context():
            connection = get_db()
            for folder, day in ((self.folder, '01'), (second, '09'), (empty, '10')):
                connection.execute('UPDATE debate_folders SET created_at=? WHERE id=?', (f'2026-10-{day}T12:00:00+00:00', folder['id']))
            for conversation, day in ((newest, '12'), (oldest, '08'), (middle, '11'), (ungrouped, '09')):
                connection.execute('UPDATE debate_sessions SET created_at=? WHERE id=?', (f'2026-10-{day}T12:00:00+00:00', conversation['id']))
            connection.commit()
        folder_ids = lambda: [item['id'] for item in self.owner.get('/api/debate/folders').json['folders']]
        self.assertEqual(folder_ids(), [self.folder['id'], second['id'], empty['id']])
        self.owner.put('/api/debate/sessions/' + middle['id'], json={**snapshot(), 'expectedRevision': middle['revision'], 'draft': 'Updated today'})
        self.owner.patch('/api/debate/folders/' + second['id'], json={'name': 'Renamed today'})
        self.assertEqual(folder_ids(), [self.folder['id'], second['id'], empty['id']])
        old_path = '/api/debate/sessions/' + oldest['id'] + '/folder'
        self.owner.patch(old_path, json={'folderId': second['id']})
        self.assertEqual(folder_ids(), [self.folder['id'], second['id'], empty['id']])
        self.owner.patch(old_path, json={'folderId': None})
        unfiled_ids = lambda: [item['id'] for item in self.owner.get('/api/debate/sessions?folder=none').json['sessions']]
        self.assertEqual(unfiled_ids(), [ungrouped['id'], oldest['id']])
        self.owner.patch('/api/debate/sessions/' + newest['id'] + '/folder', json={'folderId': None})
        self.assertEqual(folder_ids(), [second['id'], empty['id'], self.folder['id']])
        self.assertEqual(unfiled_ids(), [newest['id'], ungrouped['id'], oldest['id']])

    def test_migration_preserves_existing_conversations(self):
        # A version-15 installation has no folders or folder_id. Upgrade the real old schema.
        import sqlite3
        path = Path(self.temp.name) / 'old.db'
        connection = sqlite3.connect(path)
        connection.execute('CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)')
        for version in range(1, 16):
            connection.executescript(SCHEMA_MIGRATIONS[version])
            connection.execute('INSERT INTO schema_migrations VALUES (?)', (version,))
        connection.execute("INSERT INTO debate_sessions(id,question,participants_json,messages_json,pending_user_messages_json,draft,debate_started,paused,created_at,updated_at) VALUES ('old','Keep me','[]','[]','[]','draft',0,1,'now','now')")
        connection.commit(); connection.close()
        old_app = create_app({"TESTING": True, "AUTO_MIGRATE": True, "DB_PATH": path, "SECRET_KEY": "old", "URL_PREFIX": ""})
        with old_app.app_context():
            row = get_db().execute("SELECT question,draft,folder_id FROM debate_sessions WHERE id='old'").fetchone()
            self.assertEqual(tuple(row), ('Keep me', 'draft', None))
