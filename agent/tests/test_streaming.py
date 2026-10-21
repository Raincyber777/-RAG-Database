import json
import unittest

import httpx

from agent.gateway import DashScopeGateway, Settings
from agent.runtime import build_agent
from agent.schemas import Chunk, UserContext
from agent.services import StudyService
from test_runtime import ScriptedModel, call
from test_agent_loop import final
from test_services import Backend, Gateway


class StreamGateway(Gateway):
    def __init__(self, pieces, fail=False):
        super().__init__([{'followups': ['导数如何计算？']}])
        self.pieces = pieces
        self.fail = fail
        self.closed = False

    def stream_text(self, system, user, **kwargs):
        try:
            yield from self.pieces
            if self.fail:
                raise TimeoutError('private detail')
        finally:
            self.closed = True


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.context = UserContext('u', 't', 'n')
        self.backend = Backend([Chunk(text='导数表示变化率', ref='讲义 p1', score=.8)])

    def test_real_incremental_tokens_before_final_validation(self):
        gateway = StreamGateway(['导数', '表示变化率[', '1]。'])
        events = StudyService(self.backend, gateway).answer_stream('导数是什么', self.context)
        first = next(e for e in events if e['event'] == 'delta')
        self.assertEqual(first['data']['text'], '导数')
        self.assertFalse(gateway.closed)
        rest = list(events)
        final = rest[-1]
        self.assertEqual(final['event'], 'done')
        self.assertIn('导数表示变化率[1]', final['data']['answer'])
        self.assertFalse(final['data']['degraded'])
        self.assertEqual(final['data']['sources'][0]['title'], '讲义 p1')
        self.assertTrue(gateway.closed)

    def test_invalid_or_interrupted_output_is_replaced(self):
        for gateway in [StreamGateway(['虚构结论[88]']), StreamGateway(['未完成'], fail=True)]:
            with self.subTest(gateway=gateway):
                events = list(StudyService(self.backend, gateway).answer_stream('问题', self.context))
                replacement = next(e for e in events if e['event'] == 'replace')
                self.assertNotIn('虚构结论', replacement['data']['text'])
                self.assertIn('导数表示变化率', replacement['data']['text'])
                self.assertTrue(events[-1]['data']['degraded'])
                self.assertNotIn('private detail', str(events))

    def test_agent_stream_flows_through_actual_tool_graph(self):
        model = ScriptedModel(replies=[call('retrieve_classroom', {'query': '导数是什么'}),
            call('filter_evidence', {'keep_ids': ['1']}), final('变化率[1]。')])
        agent = build_agent(self.backend, gateway=StreamGateway(['变化率[1]。']), model=model)
        events = list(agent.stream('问题', context=self.context, thread_id='x'))
        self.assertTrue(any(e['event'] == 'delta' for e in events))
        self.assertEqual(events[-1]['event'], 'done')
        self.assertEqual(events[-1]['data']['model_call_count'], 3)
        self.assertIn('变化率', events[-1]['data']['output']['answer'])

    def test_transport_parses_sse_and_detects_missing_completion(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertTrue(payload['stream'])
            self.assertNotIn('response_format', payload)
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"你好"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(list(DashScopeGateway(Settings(api_key='test'), client).stream_text('规则', '问题')), ['你好'])
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text='data: {"choices":[{"delta":{"content":"半句"}}]}\n\n'))) as client:
            with self.assertRaises(RuntimeError):
                list(DashScopeGateway(Settings(api_key='test'), client).stream_text('规则', '问题'))

    def test_close_stops_provider_generator(self):
        gateway = StreamGateway(['第一', '第二'])
        stream = StudyService(self.backend, gateway).answer_stream('问题', self.context)
        next(e for e in stream if e['event'] == 'delta')
        stream.close()
        self.assertTrue(gateway.closed)
