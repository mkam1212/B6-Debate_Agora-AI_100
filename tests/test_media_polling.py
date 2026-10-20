import base64
import json
import os
import unittest
from unittest.mock import patch
from flask import Flask
from server.gizmoapp_server import media
from server.gizmoapp_server.media_routes import register_media_routes
from test_media import FakeResponse
import test_media


class PollingTests(unittest.TestCase):
    def test_submit_and_poll_survive_many_pending_responses_without_resubmission(self):
        responses = [FakeResponse(json.dumps({'pollTicket': 'signed-receipt', 'status': 'queued'}).encode(), 'application/json', 202)] * 85
        responses += [FakeResponse(b'RIFF0000WAVEdata', 'audio/wav')]
        with patch.dict(os.environ, test_media.CourseMediaTests().environment('audio.speech'), clear=True), patch.object(media, 'urlopen', side_effect=responses) as gateway:
            result = media.synthesize_speech('hello', wait=False)
            for _ in range(85):
                result = media.poll_media(result)
        self.assertIsInstance(result, media.GeneratedMedia)
        paths = [call.args[0].full_url for call in gateway.call_args_list]
        self.assertEqual(1, sum(p.endswith('/audio/speech') for p in paths))
        self.assertEqual(85, sum(p.endswith('/media/jobs/poll') for p in paths))
        self.assertTrue(all(call.kwargs['timeout'] <= 30 for call in gateway.call_args_list))

    def test_routes_are_prefix_aware_no_store_and_return_chunks_without_credentials(self):
        app = Flask(__name__)
        app.config['URL_PREFIX'] = '/preview/one'
        register_media_routes(app)
        client = app.test_client()
        with patch('server.gizmoapp_server.media_routes.synthesize_speech', return_value=media.PendingMedia('ticket', 'queued')) as submit:
            response = client.post('/preview/one/api/course-media/speech', json={'text': 'hello'})
        self.assertEqual(202, response.status_code)
        self.assertEqual({'pollTicket': 'ticket', 'status': 'queued'}, response.json)
        self.assertEqual('no-store', response.headers['Cache-Control'])
        self.assertFalse(submit.call_args.kwargs['wait'])
        with patch('server.gizmoapp_server.media_routes.poll_media', return_value=media.GeneratedMedia(b'RIFF0000WAVEaudio', 'audio/wav')):
            response = client.post('/preview/one/api/course-media/poll', json={'pollTicket': 'ticket'})
        self.assertEqual(200, response.status_code)
        self.assertEqual('audio/wav', response.content_type)
        self.assertEqual(b'RIFF0000WAVEaudio', response.data)

    def test_revocation_and_terminal_failure_are_not_retryable_service_unavailability(self):
        app = Flask(__name__); app.config['URL_PREFIX'] = ''
        register_media_routes(app)
        for status in [401, 403, 410, 502, 503]:
            with patch('server.gizmoapp_server.media_routes.poll_media', side_effect=media.CourseMediaError('safe error', status=status)):
                response = app.test_client().post('/api/course-media/poll', json={'pollTicket': 'ticket'})
            self.assertEqual(status, response.status_code)

    def test_malformed_browser_values_fail_without_contacting_gateway(self):
        app = Flask(__name__); app.config['URL_PREFIX'] = ''
        register_media_routes(app)
        with patch.object(media, 'urlopen') as gateway:
            for operation, payload in [('poll', {'pollTicket': None}), ('poll', {'pollTicket': []}), ('image', {'model': []})]:
                response = app.test_client().post('/api/course-media/' + operation, json=payload)
                self.assertEqual(400, response.status_code)
            gateway.assert_not_called()
