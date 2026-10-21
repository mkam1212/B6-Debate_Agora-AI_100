import io
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from pypdf import PdfWriter

from server.gizmoapp_server import create_app, debate_chat, llm
from tests.auth_helpers import authenticated_client


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.app = create_app({"TESTING": True, "DB_PATH": Path(self.temp.name) / 'app.sqlite3', "AUTO_MIGRATE": True, "SECRET_KEY": "attachment-tests", "URL_PREFIX": "/agora"}, shell_variant="text")
        self.client = authenticated_client(self.app)
        self.csrf = self.client.get('/agora/api/account/session').json['csrfToken']

    def upload(self, name, data):
        response = self.client.post('/agora/api/debate/attachments', data={'file': (io.BytesIO(data), name)}, headers={'X-CSRF-Token': self.csrf}, content_type='multipart/form-data')
        self.addCleanup(response.close)
        self.addCleanup(response.request.close)
        return response

    def png(self):
        output = io.BytesIO(); Image.new('RGB', (48, 32), '#dce8ff').save(output, format='PNG'); return output.getvalue()

    def payload(self, attachments=None):
        return {'question': 'What can we learn from these files?', 'participants': [{'id': 'maya', 'name': 'Maya', 'avatar': 'M', 'roleType': 'Analyst', 'prompt': 'Compare evidence.'}], 'messages': [], 'pendingUserMessages': [], 'draft': 'Keep my typed draft.', 'draftAttachments': attachments or [], 'debateStarted': True, 'paused': True}

    def test_image_is_uploaded_as_original_file_with_a_real_preview(self):
        original = self.png()
        response = self.upload('photo.png', original)
        self.assertEqual(response.status_code, 201, response.json)
        item = response.json['attachment']
        self.assertEqual(item['kind'], 'image'); self.assertEqual(item['size'], len(original))
        content = self.client.get(f"/agora/api/debate/attachments/{item['id']}/content")
        self.assertEqual(content.data, original)
        self.assertIn('attachment;', content.headers['Content-Disposition'])
        self.assertEqual(content.headers['X-Content-Type-Options'], 'nosniff')
        preview = self.client.get(f"/agora/api/debate/attachments/{item['id']}/preview")
        self.assertEqual(preview.mimetype, 'image/jpeg')
        self.assertEqual(Image.open(io.BytesIO(preview.data)).size, (48, 32))

    def test_document_larger_than_the_json_limit_is_a_file_not_pasted_text(self):
        original = b'Project evidence\n' * 80_000
        response = self.upload('brief.txt', original)
        self.assertEqual(response.status_code, 201, response.json)
        item = response.json['attachment']
        self.assertEqual(item['kind'], 'file')
        created = self.client.post('/agora/api/debate/sessions', json=self.payload([item])).json['session']
        self.assertEqual(created['draft'], 'Keep my typed draft.')
        self.assertEqual(created['draftAttachments'], [item])
        self.assertEqual(self.client.get(f"/agora/api/debate/attachments/{item['id']}/content").data, original)
        preview = self.client.get(f"/agora/api/debate/attachments/{item['id']}/preview").json
        self.assertIn('Project evidence', preview['text']); self.assertLessEqual(len(preview['text']), 16_000)

    def test_file_only_messages_queued_messages_and_drafts_survive_reload(self):
        item = self.upload('brief.txt', b'Actual file content.').json['attachment']
        payload = self.payload([item])
        payload['messages'] = [{'kind': 'user', 'body': '', 'attachments': [item]}]
        payload['pendingUserMessages'] = [{'id': 'queued-file', 'body': '', 'attachments': [item]}]
        response = self.client.post('/agora/api/debate/sessions', json=payload)
        self.assertEqual(response.status_code, 201, response.json)
        saved = response.json['session']
        restored = self.client.get('/agora/api/debate/sessions/' + saved['id']).json['session']
        self.assertEqual(restored['messages'][0]['body'], '')
        self.assertEqual(restored['messages'][0]['attachments'], [item])
        self.assertEqual(restored['pendingUserMessages'][0]['attachments'], [item])
        payload['draftAttachments'] = []; payload['expectedRevision'] = saved['revision']
        response = self.client.put('/agora/api/debate/sessions/' + saved['id'], json=payload)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['session']['draftAttachments'], [])

    def test_six_image_limit_is_enforced_using_server_metadata(self):
        items = [self.upload(f'{i}.png', self.png()).json['attachment'] for i in range(7)]
        payload = self.payload(items[:6])
        self.assertEqual(self.client.post('/agora/api/debate/sessions', json=payload).status_code, 201)
        payload['draftAttachments'] = [{**item, 'kind': 'file'} for item in items]
        self.assertEqual(self.client.post('/agora/api/debate/sessions', json=payload).status_code, 400)

    def test_unknown_duplicate_or_forged_attachments_are_rejected_or_canonicalized(self):
        item = self.upload('real.txt', b'Original content').json['attachment']
        for items in ([{'id': '0' * 32}], [item, item], [{'id': '../../secret'}]):
            self.assertEqual(self.client.post('/agora/api/debate/sessions', json=self.payload(items)).status_code, 400)
        forged = {**item, 'name': 'fake.exe', 'mime': 'text/html', 'size': 0}
        created = self.client.post('/agora/api/debate/sessions', json=self.payload([forged])).json['session']
        self.assertEqual(created['draftAttachments'], [item])

    def test_invalid_images_active_content_empty_and_oversized_files_are_rejected(self):
        for name, data in [('fake.png', b'<html>not an image</html>'), ('bad.svg', b'<svg onload="alert(1)"/>'), ('empty.txt', b'')]:
            self.assertEqual(self.upload(name, data).status_code, 400)
        self.assertEqual(self.upload('large.txt', b'x' * (20 * 1024 * 1024 + 1)).status_code, 413)
        self.assertEqual(self.client.get('/agora/api/debate/attachments/' + '0' * 32 + '/content').status_code, 404)

    def test_upload_requires_csrf(self):
        response = self.client.post('/agora/api/debate/attachments', data={'file': (io.BytesIO(self.png()), 'p.png')})
        self.assertEqual(response.status_code, 403)

    def test_discard_is_owner_only_csrf_protected_and_idempotent(self):
        item = self.upload('discard.txt', b'Disposable draft').json['attachment']
        path = '/agora/api/debate/attachments/' + item['id']
        self.assertEqual(self.client.delete(path).status_code, 403)
        other = authenticated_client(self.app)
        csrf = other.get('/agora/api/account/session').json['csrfToken']
        self.assertEqual(other.delete(path, headers={'X-CSRF-Token': csrf}).status_code, 404)
        self.assertEqual(self.client.get(path + '/content').status_code, 200)
        for _ in range(2):
            self.assertEqual(self.client.delete(path, headers={'X-CSRF-Token': self.csrf}).status_code, 204)
        self.assertEqual(self.client.get(path + '/content').status_code, 404)

    def test_discard_preserves_sent_files_and_other_saved_drafts(self):
        item = self.upload('evidence.txt', b'Keep this evidence').json['attachment']
        payload = self.payload([item])
        payload['messages'] = [{'kind': 'user', 'body': 'Read this.', 'attachments': [item]}]
        self.client.post('/agora/api/debate/sessions', json=payload)
        path = '/agora/api/debate/attachments/' + item['id']
        self.assertEqual(self.client.delete(path, headers={'X-CSRF-Token': self.csrf}).status_code, 204)
        self.assertEqual(self.client.get(path + '/content').status_code, 200)

    def test_removed_draft_releases_its_link_and_can_be_discarded(self):
        item = self.upload('draft.txt', b'Not sent').json['attachment']
        saved = self.client.post('/agora/api/debate/sessions', json=self.payload([item])).json['session']
        response = self.client.put('/agora/api/debate/sessions/' + saved['id'], json={**self.payload(), 'expectedRevision': saved['revision']})
        self.assertEqual(response.status_code, 200)
        path = '/agora/api/debate/attachments/' + item['id']
        self.assertEqual(self.client.delete(path, headers={'X-CSRF-Token': self.csrf}).status_code, 204)
        self.assertEqual(self.client.get(path + '/content').status_code, 404)

    def test_cleanup_removes_only_expired_orphans_and_frees_their_storage(self):
        import time
        from server.gizmoapp_server.attachments import cleanup_attachments
        from server.gizmoapp_server.db import get_db
        orphan = self.upload('old.txt', b'Abandoned upload').json['attachment']
        draft = self.upload('saved.txt', b'Saved draft').json['attachment']
        fresh = self.upload('fresh.txt', b'Fresh upload').json['attachment']
        self.client.post('/agora/api/debate/sessions', json=self.payload([draft]))
        with self.app.app_context():
            db = get_db()
            db.execute('UPDATE debate_attachments SET created_at=? WHERE id IN (?,?)',
                       (int(time.time()) - 25 * 3600, orphan['id'], draft['id']))
            db.commit()
            self.assertEqual(cleanup_attachments(), 1)
            self.assertEqual(cleanup_attachments(), 0)
            self.assertEqual(db.execute('SELECT SUM(byte_size) FROM debate_attachments').fetchone()[0], draft['size'] + fresh['size'])
        self.assertEqual(self.client.get('/agora/api/debate/attachments/' + orphan['id'] + '/content').status_code, 404)
        for item in (draft, fresh):
            self.assertEqual(self.client.get('/agora/api/debate/attachments/' + item['id'] + '/content').status_code, 200)

    def test_office_document_upload_and_pdf_without_text(self):
        output = io.BytesIO()
        with ZipFile(output, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Original document passage</w:t></w:r></w:p></w:document>')
        response = self.upload('brief.docx', output.getvalue())
        self.assertEqual(response.status_code, 201, response.json)
        preview = self.client.get(f"/agora/api/debate/attachments/{response.json['attachment']['id']}/preview").json
        self.assertIn('Original document passage', preview['text'])
        writer = PdfWriter(); writer.add_blank_page(width=200, height=200); output = io.BytesIO(); writer.write(output)
        response = self.upload('blank.pdf', output.getvalue())
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(self.client.get(f"/agora/api/debate/attachments/{response.json['attachment']['id']}/preview").json['status'], 'no-text')

    def test_model_receives_document_context_and_multimodal_image_input(self):
        photo = self.upload('photo.png', self.png()).json['attachment']
        document = self.upload('brief.txt', b'Compare transit operating costs.').json['attachment']
        payload = self.payload()
        payload['messages'] = [{'kind': 'user', 'body': 'Please compare these.', 'attachments': [photo, document]}]
        saved = self.client.post('/agora/api/debate/sessions', json=payload).json['session']
        with self.app.app_context():
            messages = debate_chat.build_reply_messages(saved, saved['participants'][0])
            validated = llm._validate_messages(messages)
        turn = next(item for item in validated if isinstance(item['content'], list))
        self.assertEqual(turn['role'], 'user')
        self.assertIn('Compare transit operating costs.', turn['content'][0]['text'])
        self.assertTrue(turn['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(saved['messages'][0]['body'], 'Please compare these.')

    def test_multimodal_content_reaches_the_existing_model_client(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Reviewed the image.'), finish_reason='stop')])
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Review this image.'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aGVsbG8='}}]}]
        with patch.object(llm, '_get_client', return_value=client), patch.object(llm, 'model_name', return_value='existing-model'):
            self.assertEqual(llm.chat(messages), 'Reviewed the image.')
        self.assertIsInstance(client.chat.completions.create.call_args.kwargs['messages'][0]['content'], list)

    def test_a_provider_without_vision_returns_an_explicit_image_error(self):
        class UnsupportedImage(Exception): status_code = 400
        client = Mock(); client.chat.completions.create.side_effect = UnsupportedImage()
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Review this image.'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aGVsbG8='}}]}]
        with patch.object(llm, '_get_client', return_value=client), patch.object(llm, 'model_name', return_value='existing-model'):
            with self.assertRaisesRegex(llm.CourseLLMError, 'vision-capable'):
                llm.chat(messages)
            with self.assertRaisesRegex(llm.CourseLLMError, 'vision-capable'):
                list(llm.stream_chat(messages))


if __name__ == '__main__': unittest.main()
