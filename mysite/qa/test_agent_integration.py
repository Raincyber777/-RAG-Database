"""Exercise real Django ORM, API views and LangGraph; mock only external models."""
import json
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from rest_framework.test import APIClient

from accounts.models import User
from notes.models import Note, NoteChunk
from qa.models import QaRecord
from agent.gateway import Settings
from agent.runtime import build_agent
from agent.schemas import UserContext
from agent.loop import QA_TOOLS


class Router(BaseChatModel):
    broken_stream: bool = False
    @property
    def _llm_type(self):
        return 'offline-test-router'

    def bind_tools(self, tools, **kwargs):
        assert {t.name for t in tools} == QA_TOOLS
        return self

    def _generate(self, messages, **kwargs):
        last = messages[-1]
        if last.type == 'tool':
            payload = json.loads(last.content)
            if 'candidates' in payload:
                reply = AIMessage(content='', tool_calls=[{'name': 'filter_evidence',
                    'args': {'keep_ids': [c['id'] for c in payload['candidates']]}, 'id': 'select', 'type': 'tool_call'}])
            else:
                reply = AIMessage(content=json.dumps({'answer': '导数是变化率[1]' if payload.get('selected') else '暂时无法确认',
                                                     'followups': ['如何求导？']}, ensure_ascii=False))
        else:
            reply = AIMessage(content='', tool_calls=[{'name': 'retrieve_classroom',
                'args': {'query': last.content}, 'id': 'retrieve', 'type': 'tool_call'}])
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _stream(self, messages, **kwargs):
        reply = self._generate(messages).generations[0].message
        if reply.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content='', tool_calls=reply.tool_calls))
        elif self.broken_stream:
            yield ChatGenerationChunk(message=AIMessageChunk(content='{"answer":"未校验且中断的答案'))
            raise TimeoutError('private provider diagnostic')
        else:
            for fragment in ('{"answer":"', '导数是', '变化率[1]", "followups":["如何求导？"]}') if '变化率' in reply.content else (reply.content,):
                yield ChatGenerationChunk(message=AIMessageChunk(content=fragment))


class Gateway:
    settings = Settings(api_key='test', enable_search=False)
    broken_stream = False

    def chat_json(self, system, user, **kwargs):
        return {'answer': '导数是变化率[1]', 'followups': ['如何求导？']}

    def stream_text(self, *args, **kwargs):
        if self.broken_stream:
            yield '未校验且中断的答案'
            raise TimeoutError('private provider diagnostic')
        yield '导数是'
        yield '变化率[1]'

    def close(self):
        pass


class AgentIntegrationTests(TransactionTestCase):
    def setUp(self):
        self.enterContext(patch('mysite.llm.chat_json', return_value=None))
        self.user = User.objects.create_user(username='alice', email='a@test.invalid', phone='1')
        self.other = User.objects.create_user(username='bob', email='b@test.invalid', phone='2')
        self.note = Note.objects.create(uploader=self.user, title='导数', course='数学', status='done',
                                        transcript='[08:15] 导数是变化率')
        NoteChunk.objects.create(note=self.note, text='导数是变化率', ref='录音 08:15', embedding=[1., 0.])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.gateway = Gateway()
        self.builds = []

        def offline_build(backend, **kwargs):
            self.builds.append(kwargs)
            return build_agent(backend, **kwargs, model=Router(broken_stream=self.gateway.broken_stream), gateway=self.gateway)

        self.builder = patch('agent.runtime.build_agent', side_effect=offline_build).start()
        self.embedding = patch('mysite.rag.llm.embed', return_value=[[1., 0.]]).start()
        self.legacy = patch('mysite.rag.answer_note', side_effect=AssertionError('legacy path used')).start()
        self.addCleanup(patch.stopall)
        patch('agent.gateway.Settings.from_env', return_value=Settings(
            api_key='', tavily_api_key='offline-tavily', search_provider='tavily')).start()

    def ask(self, stream=False, **extra):
        return self.client.post('/api/qa/ask/stream/' if stream else '/api/qa/ask/',
                                {'note_id': self.note.pk, 'question': '什么是导数？', **extra}, format='json')

    def events(self, response):
        try:
            raw = b''.join(response.streaming_content).decode()
        finally:
            response.close()
        return [(block.splitlines()[0][7:], json.loads(block.splitlines()[1][6:]))
                for block in raw.strip().split('\n\n')]

    def test_sync_runs_agent_and_persists_existing_record_contract(self):
        response = self.ask()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response['X-QA-Engine'], 'agent')
        record = QaRecord.objects.get()
        self.assertEqual(response.data['answer'], record.answer)
        self.assertIn('变化率[1]', record.answer)
        self.assertEqual(record.references[0]['time'], '08:15')
        self.assertEqual(record.references[0]['kind'], 'kb')
        self.assertEqual(record.followups, ['如何求导？'])
        self.assertEqual(len(self.builds), 1)
        self.assertEqual(self.builds[0]['tool_names'], QA_TOOLS)

    def test_stream_has_deltas_and_one_done_with_saved_record(self):
        response = self.ask(stream=True)
        self.assertEqual(response['X-QA-Engine'], 'agent')
        events = self.events(response)
        self.assertIn(('delta', {'text': '导数是', 'provisional': True}), events)
        done = [data for name, data in events if name == 'done']
        self.assertEqual(len(done), 1)
        record = QaRecord.objects.get()
        self.assertEqual(done[0]['id'], record.pk)
        self.assertEqual(done[0]['answer'], record.answer)
        self.assertEqual([d['text'] for n, d in events if n == 'replace'][-1], record.answer)

    def test_partial_model_failure_is_replaced_before_saving(self):
        self.gateway.broken_stream = True
        events = self.events(self.ask(stream=True))
        self.assertTrue(any('未校验' in str(d) for n, d in events if n == 'delta'))
        final = QaRecord.objects.get().answer
        self.assertIn('暂时无法确认', final)
        self.assertNotIn('未校验', final)
        self.assertNotIn('private', str(events))
        self.assertEqual(events[-1][1]['answer'], final)

    def test_invalid_foreign_and_unfinished_notes_never_fall_back(self):
        foreign = Note.objects.create(uploader=self.other, title='private', status='done')
        pending = Note.objects.create(uploader=self.user, title='pending')
        for stream in (False, True):
            for pk, code in ((foreign.pk, 404), (99999, 404), (0, 400), (pending.pk, 400)):
                with self.subTest(stream=stream, pk=pk):
                    self.assertEqual(self.ask(stream=stream, note_id=pk).status_code, code)
        self.assertFalse(QaRecord.objects.exists())
        self.builder.assert_not_called()

    def test_unauthenticated_request_is_rejected(self):
        self.client = APIClient()
        self.assertIn(self.ask().status_code, (401, 403))
        self.builder.assert_not_called()

    def test_missing_note_selects_own_latest_completed_note(self):
        response = self.ask(note_id=None)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['note'], self.note.pk)

    def test_admin_can_ask_about_another_users_note(self):
        self.other.role = 'admin'
        self.other.save(update_fields=['role'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.ask().status_code, 201)

    def test_adapter_rejects_forged_context_and_revoked_access(self):
        from qa.agent_bridge import DjangoNoteBackend
        backend = DjangoNoteBackend(self.user, self.note)
        for ctx in (UserContext(str(self.other.pk), 'classroom', str(self.note.pk)),
                    UserContext(str(self.user.pk), 'other', str(self.note.pk))):
            with self.assertRaises(PermissionError):
                backend.material(ctx)
        self.note.uploader = self.other
        self.note.save(update_fields=['uploader'])
        with self.assertRaises(PermissionError):
            backend.retrieve('导数', backend.context)

    @override_settings(AGENT_ENABLED=False)
    def test_disabled_switch_preserves_legacy_path(self):
        self.legacy.side_effect = None
        self.legacy.return_value = ('旧回答', [], [])
        response = self.ask()
        self.assertEqual(response.data['answer'], '旧回答')
        self.assertEqual(response['X-QA-Engine'], 'legacy')
        self.builder.assert_not_called()

    @override_settings(DASHSCOPE_API_KEY='')
    def test_missing_key_uses_legacy_without_building_agent(self):
        self.legacy.side_effect = None
        self.legacy.return_value = None
        response = self.ask()
        self.assertEqual(response['X-QA-Engine'], 'legacy')
        self.assertIn('演示检索', response.data['answer'])
        self.builder.assert_not_called()

    def test_initialization_failure_saves_safe_answer_without_second_model_call(self):
        self.builder.side_effect = RuntimeError('private configuration')
        response = self.ask()
        self.assertEqual(response.status_code, 201)
        self.assertIn('暂时无法确认', response.data['answer'])
        self.assertNotIn('private', response.data['answer'])
        self.legacy.assert_not_called()

    def test_stream_initialization_failure_still_finishes_with_saved_safe_record(self):
        self.builder.side_effect = RuntimeError('private configuration')
        events = self.events(self.ask(stream=True))
        self.assertEqual([name for name, _ in events], ['replace', 'done'])
        self.assertEqual(events[0][1]['text'], QaRecord.objects.get().answer)
        self.assertNotIn('private', str(events))

    def test_disconnect_closes_stream_without_saving_provisional_text(self):
        response = self.ask(stream=True)
        events = iter(response.streaming_content)
        while True:
            frame = next(events).decode()
            if 'event: delta\n' in frame:
                break
        self.assertFalse(QaRecord.objects.exists())
        response.close()
        self.assertFalse(QaRecord.objects.exists())

    def test_missing_package_preserves_legacy_path(self):
        self.legacy.side_effect = None
        self.legacy.return_value = ('旧回答', [], [])
        with patch('qa.agent_bridge.find_spec', return_value=None):
            response = self.ask()
        self.assertEqual(response['X-QA-Engine'], 'legacy')
        self.builder.assert_not_called()

    @override_settings(AGENT_ENABLED=False)
    def test_legacy_stream_retains_record_shape(self):
        with patch('mysite.rag.answer_stream', return_value=iter([
            {'type': 'delta', 'text': '旧回答'}, {'type': 'end', 'refs': []},
        ])), patch('qa.views._stream_followups', return_value=[]):
            events = self.events(self.ask(stream=True))
        self.assertEqual(events[-1][1]['answer'], '旧回答')
        self.assertEqual(QaRecord.objects.count(), 1)
        self.builder.assert_not_called()

    def test_disabled_user_is_denied_before_embedding(self):
        from qa.agent_bridge import DjangoNoteBackend
        backend = DjangoNoteBackend(self.user, self.note)
        self.user.status = 'disabled'
        self.user.save(update_fields=['status'])
        with self.assertRaises(PermissionError):
            backend.retrieve('导数', backend.context)
        self.embedding.assert_not_called()

    def test_external_sources_preserve_verified_url_and_identifier(self):
        from qa.agent_bridge import record_data
        output = record_data({'answer': '补充[E1]', 'sources': [{
            'id': 'E1', 'kind': 'external', 'title': '已校验来源', 'url': 'https://example.org/source',
        }]})
        self.assertEqual(output['references'][0], {
            'label': '已校验来源', 'time': '', 'detail': '已校验来源', 'kind': 'external',
            'id': 'E1', 'url': 'https://example.org/source',
        })

    def test_agent_configuration_keeps_tavily_key_from_env(self):
        self.ask()
        config = self.builds[0]['settings']
        self.assertEqual(config.tavily_api_key, 'offline-tavily')
        self.assertEqual(config.search_provider, 'tavily')
        self.assertEqual(config.api_key, 'offline-only')

    def test_general_qa_without_notes_uses_legacy_and_keeps_session_ref(self):
        self.note.delete()
        self.gateway.search = lambda question: None
        with patch('mysite.rag.answer_general', return_value=('通用回答', [], [])) as general:
            response = self.ask(note_id=None, session_ref='qa-general')
        general.assert_called_once_with('什么是导数？')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response['X-QA-Engine'], 'legacy')
        self.assertEqual(response.data['answer'], '通用回答')
        self.assertIsNone(response.data['note'])
        self.assertEqual(QaRecord.objects.get().session_ref, 'qa-general')
        self.assertEqual(len(self.builds), 0)
        self.embedding.assert_not_called()

    def test_stream_preserves_session_ref(self):
        events = self.events(self.ask(stream=True, session_ref='qa-stream'))
        self.assertEqual(events[-1][1]['session_ref'], 'qa-stream')
        self.assertEqual(QaRecord.objects.get().session_ref, 'qa-stream')

    def test_general_stream_has_nullable_note_and_done(self):
        self.note.delete()
        self.gateway.search = lambda question: None
        with patch('mysite.rag.answer_stream_general', return_value=iter([
                {'type': 'delta', 'text': '通用流式回答'}, {'type': 'end', 'refs': []}])) as general:
            response = self.ask(stream=True, note_id=None, session_ref='qa-general')
            self.assertEqual(response['X-QA-Engine'], 'legacy')
            events = self.events(response)
        general.assert_called_once_with('什么是导数？')
        self.assertEqual(len(self.builds), 0)
        self.assertEqual(events[-1][1]['answer'], '通用流式回答')
        self.assertEqual(events[-1][0], 'done')
        self.assertIsNone(events[-1][1]['note'])
        self.assertEqual(events[-1][1]['session_ref'], 'qa-general')

    def test_general_code_generation_bypasses_external_search(self):
        self.note.delete()
        question = ('编写一个函数 find_max_and_sum(*args)，该函数可以接收任意多个整数作为参数，'
                    '并返回其中的最大值以及所有整数的总和。请在主程序中调用该函数并打印结果。')
        answer = 'def find_max_and_sum(*args):\n    return max(args), sum(args)'
        with patch('mysite.rag.answer_code_request', return_value=answer) as direct, \
                patch('mysite.rag.answer_general', side_effect=AssertionError('external retrieval path used')):
            response = self.ask(note_id=None, session_ref='qa-code', question=question)
        direct.assert_called_once()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['answer'], answer)
        self.assertEqual(response.data['references'], [])
        self.assertFalse(response.data['used_external'])

    def test_general_code_generation_stream_bypasses_external_search(self):
        self.note.delete()
        question = ('编写一个函数 find_max_and_sum(*args)，该函数可以接收任意多个整数作为参数，'
                    '并返回其中的最大值以及所有整数的总和。请在主程序中调用该函数并打印结果。')
        answer = 'def find_max_and_sum(*args):\n    return max(args), sum(args)'
        events_out = iter([{'type': 'delta', 'text': answer}, {'type': 'end', 'refs': []}])
        with patch('mysite.rag.answer_code_stream', return_value=events_out) as direct, \
                patch('mysite.rag.answer_stream_general', side_effect=AssertionError('external retrieval path used')):
            response = self.ask(stream=True, note_id=None, session_ref='qa-code-stream', question=question)
            events = self.events(response)
        direct.assert_called_once()
        self.assertEqual(events[-1][0], 'done')
        self.assertEqual(events[-1][1]['answer'], answer)
        self.assertEqual(events[-1][1]['references'], [])

    def test_selected_note_code_generation_bypasses_classroom_agent(self):
        question = '编写一个函数 find_max_and_sum(*args)，返回最大值和总和。'
        answer = 'def find_max_and_sum(*args):\n    return max(args), sum(args)'
        with patch('mysite.rag.answer_code_request', return_value=answer) as direct:
            response = self.ask(question=question)
        direct.assert_called_once()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response['X-QA-Engine'], 'code')
        self.assertEqual(response.data['answer'], answer)
        self.assertEqual(len(self.builds), 0)
        self.embedding.assert_not_called()

    def test_selected_note_code_generation_stream_bypasses_classroom_agent(self):
        question = '编写一个函数 find_max_and_sum(*args)，返回最大值和总和。'
        answer = 'def find_max_and_sum(*args):\n    return max(args), sum(args)'
        events_out = iter([{'type': 'delta', 'text': answer}, {'type': 'end', 'refs': []}])
        with patch('mysite.rag.answer_code_stream', return_value=events_out) as direct:
            response = self.ask(stream=True, question=question)
            events = self.events(response)
        direct.assert_called_once()
        self.assertEqual(response['X-QA-Engine'], 'code')
        self.assertEqual(events[-1][0], 'done')
        self.assertEqual(events[-1][1]['answer'], answer)
        self.assertEqual(len(self.builds), 0)

    def test_code_request_detection_requires_an_implementation_request(self):
        from mysite import rag
        question = ('编写一个函数 find_max_and_sum(*args)，该函数可以接收任意多个整数作为参数，'
                    '并返回其中的最大值以及所有整数的总和。请在主程序中调用该函数并打印结果。')
        self.assertTrue(rag.is_code_generation_request(question))
        self.assertTrue(rag.is_code_generation_request('Implement a Python function that returns the largest value.'))
        self.assertFalse(rag.is_code_generation_request('Python 函数为什么可以返回多个值？'))

    def test_code_answer_uses_chat_model_without_search(self):
        from mysite import rag
        question = '编写一个函数，返回整数列表的最大值。'
        with patch('mysite.rag.llm.chat', return_value='```python\nmax(values)\n```') as chat, \
                patch('mysite.rag._pick_findings', side_effect=AssertionError('search must not run')):
            answer = rag.answer_code_request(question)
        self.assertIn('max(values)', answer)
        kwargs = chat.call_args.kwargs
        self.assertEqual(kwargs['model'], rag.llm.MODEL_CHAT)
        messages = chat.call_args.args[0]
        self.assertIn('不要要求联网资料', messages[0]['content'])
        self.assertEqual(messages[-1]['content'], question)

    def test_code_answer_stream_emits_content_without_search(self):
        from mysite import rag
        with patch('mysite.rag.llm.chat_stream', side_effect=lambda *a, **k: (part for part in ['def f():', '\n    return 1'])) as chat_stream, \
                patch('mysite.rag._pick_findings', side_effect=AssertionError('search must not run')):
            events = list(rag.answer_code_stream('写一个返回 1 的 Python 函数。'))
        self.assertEqual([event['text'] for event in events if event['type'] == 'delta'],
                         ['def f():', '\n    return 1'])
        self.assertEqual(events[-1], {'type': 'end', 'refs': []})
        self.assertEqual(chat_stream.call_args.kwargs['model'], rag.llm.MODEL_CHAT)

    @override_settings(DASHSCOPE_API_KEY='')
    def test_agent_model_key_can_come_from_project_env(self):
        with patch('agent.gateway.Settings.from_env', return_value=Settings(api_key='env-model', tavily_api_key='env-search')):
            response = self.ask()
        self.assertEqual(response['X-QA-Engine'], 'agent')
        self.assertEqual(self.builds[0]['settings'].api_key, 'env-model')
