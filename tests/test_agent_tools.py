import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import patch

from server.gizmoapp_server import create_app, agent_tools, debate_chat, llm
from server.gizmoapp_server.debate_sessions import create_session, fetch_session
from server.gizmoapp_server.db import get_db
from tests.auth_helpers import authenticated_client


class FakeStream:
    def __init__(self, events): self.events, self.closed = events, False
    def __iter__(self): return iter(self.events)
    def close(self): self.closed = True


def event(kind, **values): return N(type=kind, **values)


class AgentToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'AUTO_MIGRATE': True, 'DB_PATH': Path(self.tmp.name)/'test.sqlite3',
            'SECRET_KEY': 'tools-test', 'WEB_SEARCH_ENABLED': True, 'GIZMO_LLM_BASE_URL': 'https://api.openai.com/v1',
            'GIZMO_LLM_API_KEY': 'test-key', 'GIZMO_LLM_MODEL': 'test-model'}, shell_variant='text')
        self.ctx = self.app.app_context(); self.ctx.push()
        self.role = {'id': 'role-1', 'name': 'Grace', 'identity': 'Evidence analyst', 'avatar': 'G', 'roleType': 'Analyst', 'prompt': 'Test the last claim.'}
        self.snapshot = {'question': 'Search official documentation for function calling.', 'participants': [self.role], 'messages': [], 'pendingUserMessages': [], 'draft': '', 'draftAttachments': [], 'paused': False, 'debateStarted': True}
        self.session = create_session(get_db(), self.snapshot)

    def tearDown(self): self.ctx.pop(); self.tmp.cleanup()

    def native(self, stream):
        client = N(with_options=lambda **kw: N(responses=N(create=lambda **kwargs: self.capture(kwargs, stream))))
        return patch.object(llm, '_get_client', return_value=client)

    def capture(self, kwargs, stream): self.arguments = kwargs; return stream

    def searched_stream(self):
        body = 'The application executes the function. [Docs](https://example.org/docs?utm_source=openai)'
        annotation = {'type': 'url_citation', 'url': 'https://example.org/docs?utm_source=openai', 'title': 'Docs', 'start_index': body.index('[Docs]'), 'end_index': len(body)}
        return FakeStream([event('response.web_search_call.searching'), event('response.web_search_call.completed'),
            event('response.output_text.delta', delta=body[:16]), event('response.output_text.delta', delta=body[16:]),
            event('response.output_text.annotation.added', annotation=annotation), event('response.completed', response=N(status='completed'))])

    def test_real_tool_events_keep_streaming_and_register_only_annotated_sources(self):
        stream = self.searched_stream()
        with self.native(stream):
            events = agent_tools.stream_reply(self.session, [{'role': 'system', 'content': 'Selected: Grace'}, {'role': 'user', 'content': self.snapshot['question']}])
            self.assertEqual(next(events), {'type': 'activity', 'phase': 'searching'})
            self.assertEqual(next(events)['phase'], 'reading')
            self.assertEqual(next(events)['phase'], 'answering')
            self.assertEqual(next(events)['text'], 'The application ')
            remainder = list(events)
        self.assertTrue(stream.closed)
        final = remainder[-1]
        self.assertEqual(final['text'], 'The application executes the function. [1]')
        self.assertEqual(final['citations'][0]['url'], 'https://example.org/docs')
        self.assertEqual(self.arguments['tool_choice'], {'type': 'web_search'})
        self.assertEqual(self.arguments['max_tool_calls'], 2)
        self.assertFalse(self.arguments['store'])
        self.assertEqual(len(agent_tools.evidence(self.session['id'])), 1)
        other = create_session(get_db(), self.snapshot)
        self.assertEqual(agent_tools.evidence(other['id']), [])

    def test_personal_opinion_can_answer_without_any_search_or_fake_sources(self):
        session = {**self.session, 'question': 'I prefer a smaller team. What do you think?'}
        stream = FakeStream([event('response.output_text.delta', delta='A smaller team could make decisions faster.'), event('response.completed', response=N(status='completed'))])
        with self.native(stream): final = list(agent_tools.stream_reply(session, [{'role':'user','content':session['question']}]))[-1]
        self.assertEqual(self.arguments['tool_choice'], 'auto')
        self.assertEqual(final['citations'], [])
        self.assertEqual(agent_tools.evidence(session['id']), [])

    def test_shared_sources_reuse_identity_without_searching_again(self):
        with self.native(self.searched_stream()): list(agent_tools.stream_reply(self.session, [{'role':'user','content':'Find the docs.'}]))
        source = agent_tools.evidence(self.session['id'])[0]
        body = f'That still leaves the application responsible. [S:{source["source_id"]}]'
        stream = FakeStream([event('response.output_text.delta',delta=body),event('response.completed',response=N(status='completed'))])
        with self.native(stream): final = list(agent_tools.stream_reply(self.session,[{'role':'user','content':'Consider the evidence.'}]))[-1]
        self.assertEqual(self.arguments['tool_choice'], 'auto')
        self.assertEqual(final['text'], 'That still leaves the application responsible. [1]')
        self.assertEqual(final['citations'][0]['sourceId'], source['source_id'])
        self.assertIn('untrusted response excerpts', self.arguments['input'][1]['content'])

    def test_no_web_instruction_overrides_time_sensitive_search_and_new_requests_get_fresh_lookup(self):
        session = {**self.session, 'messages':[{'kind':'user','id':'m1','body':'不要联网，只分析我提供的资料'}]}
        self.assertEqual(agent_tools.search_policy(session, []), 'none')
        source = {'request_scope': agent_tools.request_scope(session), 'fetched_at': 9999999999}
        session['messages'].append({'kind':'user','id':'m2','body':'Search again for the latest rules.'})
        self.assertEqual(agent_tools.search_policy(session,[source]), 'required')

    def test_images_and_original_role_directory_survive_the_tool_adapter(self):
        messages = [{'role':'system','content':'Selected speaker and source directory.'}, {'role':'user','content':[
            {'type':'text','text':'Review this supplied image, not web evidence.'},
            {'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]}]
        result = agent_tools._response_messages(messages,[])
        self.assertEqual(result[0], messages[0])
        self.assertEqual(result[2]['content'][1]['type'],'input_image')
        self.assertEqual(result[2]['content'][1]['image_url'],'data:image/png;base64,YQ==')
        self.assertIn('untrusted reference data',result[1]['content'])

    def test_forged_client_citation_cannot_acquire_a_retrieved_identity(self):
        messages = [{'citations':[{'sourceId':'web-'+'a'*20,'url':'https://invented.example/report','title':'Invented','fetchedAt':123}]}]
        agent_tools.bind_citations(self.session['id'],messages)
        self.assertNotIn('sourceId', messages[0]['citations'][0])
        with self.native(self.searched_stream()): list(agent_tools.stream_reply(self.session,[{'role':'user','content':'Search.'}]))
        messages = [{'citations':[{'url':'https://example.org/docs','title':'Altered by client','excerpt':'Made up'}]}]
        agent_tools.bind_citations(self.session['id'],messages)
        self.assertEqual(messages[0]['citations'][0]['title'], 'Docs')
        self.assertNotEqual(messages[0]['citations'][0]['excerpt'], 'Made up')

    def test_unavailable_tools_do_not_claim_verification_and_partial_failure_is_not_retried(self):
        with patch.object(llm, '_get_client', side_effect=RuntimeError('private transport detail')), patch.object(llm,'stream_chat',return_value=iter(['I couldn’t verify this online.'])) as fallback:
            output = list(agent_tools.stream_reply(self.session,[{'role':'user','content':'Search.'}]))
            self.assertEqual(output[0]['phase'],'unavailable')
            self.assertIn('Do not assert current facts', fallback.call_args.args[0][1]['content'])
        bad = FakeStream([event('response.output_text.delta',delta='A partial thought'),event('response.incomplete')])
        with self.native(bad), patch.object(llm,'stream_chat') as fallback:
            with self.assertRaises(llm.CourseLLMError): list(agent_tools.stream_reply(self.session,[{'role':'user','content':'Search.'}]))
            fallback.assert_not_called()
        self.assertEqual(agent_tools.evidence(self.session['id']),[])

    def test_unicode_offsets_do_not_remove_the_claim_and_unsafe_sources_are_rejected(self):
        text = '🙂 应用负责执行。 [文档](https://example.org/docs)'
        a = {'type':'url_citation','url':'https://example.org/docs','start_index':text.index('[文档]'),'end_index':len(text)}
        result = agent_tools.cited_text(text,[a],[{'url':a['url'],'sourceId':'web-'+'a'*20}])
        self.assertEqual(result,'🙂 应用负责执行。 [1]')
        wrapped = 'The application executes it. ([example.org](https://example.org/docs?utm_source=openai))'
        self.assertEqual(agent_tools.cited_text(wrapped, [], [{'url':a['url'],'sourceId':'web-'+'a'*20}]), 'The application executes it. [1]')
        linked_claim = '[The application executes it](https://example.org/docs).'
        self.assertEqual(agent_tools.cited_text(linked_claim, [], [{'url':a['url'],'sourceId':'web-'+'a'*20}]), 'The application executes it [1].')
        for url in ('http://127.0.0.1/a','http://[::1]/','https://a.local/','https://user:pass@example.org/','javascript:alert(1)'):
            self.assertIsNone(agent_tools.source_url(url))

    def test_sse_identity_sources_persistence_and_one_reply_allowance(self):
        client = authenticated_client(self.app)
        session = client.post('/api/debate/sessions',json=self.snapshot).get_json()['session']
        with self.native(self.searched_stream()):
            response = client.post(f'/api/debate/sessions/{session["id"]}/reply',json={'participantId':'role-1'},headers={'Accept':'text/event-stream'})
            events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith('data: ')]
        self.assertEqual(events[0]['speakerId'],'role-1')
        self.assertEqual(events[-1]['type'],'done')
        sources = next(e for e in events if e['type']=='citations')
        payload = {**self.snapshot,'expectedRevision':session['revision'],'messages':[{'kind':'participant','roleId':'role-1','letter':'A','body':sources['text'],'citations':sources['citations']}]}
        saved = client.put(f'/api/debate/sessions/{session["id"]}',json=payload)
        self.assertEqual(saved.status_code,200)
        restored = client.get(f'/api/debate/sessions/{session["id"]}').get_json()['session']
        self.assertTrue(restored['messages'][0]['citations'][0]['sourceId'].startswith('web-'))
        usage = client.get('/api/account/usage').get_json()['usage']
        self.assertEqual(usage['used'],1)
        self.assertEqual(usage['remaining'],usage['starterAllowance']-1)

    def test_failed_tool_reply_refunds_allowance_and_never_emits_done(self):
        client = authenticated_client(self.app)
        session = client.post('/api/debate/sessions',json=self.snapshot).get_json()['session']
        stream = FakeStream([event('response.output_text.delta',delta='Not finished'),event('response.failed')])
        with self.native(stream):
            response = client.post(f'/api/debate/sessions/{session["id"]}/reply',json={'participantId':'role-1'},headers={'Accept':'text/event-stream'})
            events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith('data: ')]
        self.assertEqual(events[-1]['type'],'error')
        self.assertFalse(any(e['type']=='done' for e in events))
        usage = client.get('/api/account/usage').get_json()['usage']
        self.assertEqual(usage['used'],0)
        self.assertEqual(usage['remaining'],usage['starterAllowance'])
