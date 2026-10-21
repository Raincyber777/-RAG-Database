import unittest
import json

import httpx
from langchain_openai import ChatOpenAI

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from agent.runtime import build_agent, scoped_thread_id
from agent.schemas import UserContext
from test_services import Backend, Gateway


class ScriptedModel(BaseChatModel):
    replies: list[AIMessage]
    _index: int = PrivateAttr(default=0)
    _seen: list = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self):
        return 'offline-scripted'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._seen.append(list(messages))
        reply = self.replies[self._index]
        self._index += 1
        return ChatResult(generations=[ChatGeneration(message=reply)])


def call(name, args=None):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args or {}, 'id': 'call_test', 'type': 'tool_call'}])


def task_done():
    return AIMessage(content='{"task_result":true}')


class RuntimeTests(unittest.TestCase):
    def test_host_can_limit_tools_and_override_router_prompt(self):
        class RecordingModel(ScriptedModel):
            _names: list = PrivateAttr(default_factory=list)

            def bind_tools(self, tools, **kwargs):
                self._names.extend(t.name for t in tools)
                return self

        model = RecordingModel(replies=[call('answer_question', {'question': '导数？'}), task_done()])
        agent = build_agent(Backend(), gateway=Gateway(), model=model,
                            tool_names={'answer_question'}, system_prompt='只调用课堂问答工具')
        result = agent.invoke('导数？', context=UserContext('u', 't'), thread_id='x')
        self.assertEqual(model._names, ['answer_question', 'answer_question'])
        self.assertEqual(model._seen[0][0].content, '只调用课堂问答工具')
        self.assertIn('answer', result['output'])

    def test_invalid_tool_allowlist_fails_closed(self):
        for names in (set(), {'unknown'}):
            with self.subTest(names=names), self.assertRaises(ValueError):
                build_agent(Backend(), gateway=Gateway(), model=ScriptedModel(replies=[]), tool_names=names)

    def test_answer_tool_executes_and_counts_real_router_calls(self):
        model = ScriptedModel(replies=[call('answer_question', {'question': '什么是导数？'}), task_done()])
        agent = build_agent(Backend(), gateway=Gateway(), model=model)
        result = agent.invoke('什么是导数？', context=UserContext('u', 't', 'n'), thread_id='chat')
        self.assertIn('暂时无法确认', result['output']['answer'])
        self.assertEqual(result['model_call_count'], 2)
        self.assertEqual(len(model._seen), 2)
        self.assertEqual(model._seen[1][-1].type, 'tool')

    def test_preferences_and_same_named_threads_are_isolated(self):
        model = ScriptedModel(replies=[
            call('save_user_preference', {'name': 'explanation_style', 'value': '举例说明'}),
            task_done(), call('get_user_preferences'), task_done(), call('get_user_preferences'), task_done(),
        ])
        agent = build_agent(Backend(), gateway=Gateway(), model=model)
        alice = UserContext('a', 'tenant', 'n')
        bob = UserContext('b', 'tenant', 'n')
        agent.invoke('记住我喜欢举例说明', context=alice, thread_id='same')
        other = agent.invoke('我的偏好', context=bob, thread_id='same')
        own = agent.invoke('我的偏好', context=alice, thread_id='same')
        self.assertEqual(other['output']['preferences'], {})
        self.assertEqual(own['output']['preferences'], {'explanation_style': '举例说明'})
        self.assertEqual(other['model_call_count'], 2)
        self.assertEqual(own['model_call_count'], 4)
        self.assertNotIn('举例说明', str(model._seen[2]))

    def test_thread_scope_includes_tenant_note_and_ambiguous_separators(self):
        scopes = [scoped_thread_id(UserContext('a', 't', 'n'), 'x'), scoped_thread_id(UserContext('a', 'other', 'n'), 'x'), scoped_thread_id(UserContext('a', 't', 'other'), 'x'), scoped_thread_id(UserContext('a:t', 'b', 'n'), 'x'), scoped_thread_id(UserContext('a', 't:b', 'n'), 'x')]
        self.assertEqual(len(set(scopes)), 5)

    def test_backend_access_denial_does_not_leak_exception(self):
        class Denied(Backend):
            def material(self, context):
                raise PermissionError('sensitive database details')
        model = ScriptedModel(replies=[call('summarize_notes'), task_done()])
        result = build_agent(Denied(), gateway=Gateway(), model=model).invoke('生成纪要', context=UserContext('u', 't'), thread_id='x')
        self.assertTrue(result['output']['degraded'])
        self.assertNotIn('sensitive', str(result))

    def test_model_cannot_bypass_grounding_with_free_text(self):
        model = ScriptedModel(replies=[AIMessage(content='这是没有依据的课程结论'), AIMessage(content='{"answer":"暂时无法确认","followups":[]}')])
        result = build_agent(Backend(), gateway=Gateway(), model=model).invoke('提问', context=UserContext('u', 't'), thread_id='x')
        self.assertNotIn('没有依据的课程结论', str(result['output']))
        self.assertTrue(result['output']['degraded'])

    def test_router_failure_is_a_safe_degraded_result(self):
        class BrokenModel(ScriptedModel):
            def _generate(self, *args, **kwargs):
                raise TimeoutError('provider returned sensitive details')
        with self.assertLogs('agent.runtime', level='WARNING'):
            result = build_agent(Backend(), gateway=Gateway(), model=BrokenModel(replies=[])).invoke('问题', context=UserContext('u', 't'), thread_id='x')
        self.assertTrue(result['output']['degraded'])
        self.assertNotIn('sensitive', str(result))

    def test_saved_style_reaches_answer_service_as_data(self):
        from agent.schemas import Chunk
        model = ScriptedModel(replies=[call('save_user_preference', {'name': 'explanation_style', 'value': '举例说明'}), task_done(), call('answer_question', {'question': '导数是什么？'}), task_done()])
        gateway = Gateway([{'answer': '导数是变化率[1]', 'followups': []}])
        agent = build_agent(Backend([Chunk(text='导数是变化率', ref='讲义', score=.9)]), gateway=gateway, model=model)
        ctx = UserContext('u', 't', 'n')
        agent.invoke('记住我喜欢举例', context=ctx, thread_id='x')
        agent.invoke('导数是什么？', context=ctx, thread_id='x')
        self.assertIn('explanation_style', gateway.calls[0][1])
        self.assertIn('举例说明', gateway.calls[0][1])

    def test_openai_transport_uses_tools_without_exposing_runtime_identity_args(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            if len(requests) == 2:
                return httpx.Response(200, json={'id': 'final', 'object': 'chat.completion', 'created': 1, 'model': 'qwen-plus',
                    'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{"task_result":true}'}}]})
            return httpx.Response(200, json={
                'id': 'chat-test', 'object': 'chat.completion', 'created': 1, 'model': 'qwen-plus',
                'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
                    'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {'name': 'answer_question', 'arguments': '{"question":"导数是什么？"}'}}],
                }}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20},
            })
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        model = ChatOpenAI(model='qwen-plus', api_key='test-only', base_url='https://example.org/v1', http_client=client, max_retries=0)
        result = build_agent(Backend(), gateway=Gateway(), model=model).invoke('导数是什么？', context=UserContext('u', 't'), thread_id='x')
        self.assertIn('暂时无法确认', result['output']['answer'])
        self.assertEqual(len(requests), 2)
        names = {tool['function']['name'] for tool in requests[0]['tools']}
        self.assertIn('generate_quiz', names)
        self.assertEqual(len(names), 11)
        for tool in requests[0]['tools']:
            properties = tool['function']['parameters'].get('properties', {})
            self.assertFalse({'runtime', 'user_id', 'tenant_id'} & set(properties))


if __name__ == '__main__':
    unittest.main()
