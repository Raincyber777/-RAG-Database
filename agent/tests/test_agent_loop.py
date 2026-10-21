import json
import unittest

from langchain_core.messages import AIMessage

from agent.runtime import build_agent
from agent.schemas import Chunk, UserContext, SearchResult, SearchSource
from test_runtime import ScriptedModel, call
from test_services import Backend, Gateway


def final(text):
    return AIMessage(content=json.dumps({'answer': text, 'followups': []}, ensure_ascii=False))


class AgentLoopTests(unittest.TestCase):
    context = UserContext('u', 't', 'n')

    def test_model_requeries_reads_tool_results_filters_then_answers(self):
        class SearchBackend(Backend):
            queries = []

            def retrieve(self, question, context, limit=4):
                self.queries.append(question)
                return [] if len(self.queries) == 1 else [Chunk(text='导数是瞬时变化率', ref='录音 08:15', score=.9)]

        backend = SearchBackend()
        model = ScriptedModel(replies=[
            call('retrieve_classroom', {'query': '导数'}),
            call('retrieve_classroom', {'query': '瞬时变化率的定义'}),
            call('filter_evidence', {'keep_ids': ['1']}), final('导数是瞬时变化率[1]'),
        ])
        result = build_agent(backend, gateway=Gateway(), model=model).invoke('导数是什么', context=self.context, thread_id='x')
        self.assertEqual(backend.queries, ['导数', '瞬时变化率的定义'])
        self.assertEqual(result['model_call_count'], 4)
        self.assertEqual(model._seen[1][-1].type, 'tool')
        self.assertIn('瞬时变化率', model._seen[2][-1].content)
        self.assertFalse(result['output']['degraded'])
        self.assertEqual(result['output']['sources'][0]['timestamp_seconds'], 495)

    def test_invalid_citation_is_returned_to_model_for_repair(self):
        model = ScriptedModel(replies=[call('retrieve_classroom', {'query': '导数'}),
            call('filter_evidence', {'keep_ids': ['1']}), final('编造[99]'), final('导数是变化率[1]')])
        backend = Backend([Chunk(text='导数是变化率', ref='讲义', score=.9)])
        result = build_agent(backend, gateway=Gateway(), model=model).invoke('导数', context=self.context, thread_id='x')
        self.assertEqual(result['model_call_count'], 4)
        self.assertNotIn('编造', result['output']['answer'])
        self.assertIn('校验', model._seen[-1][-1].content)

    def test_dropped_evidence_cannot_be_cited(self):
        backend = Backend([Chunk(text='导数是变化率', ref='讲义', score=.9), Chunk(text='其他内容', ref='其他', score=.8)])
        model = ScriptedModel(replies=[call('retrieve_classroom', {'query': '导数'}),
            call('filter_evidence', {'keep_ids': ['1']}), final('其他内容[2]'), final('导数是变化率[1]')])
        result = build_agent(backend, gateway=Gateway(), model=model).invoke('导数', context=self.context, thread_id='x')
        self.assertEqual([s['id'] for s in result['output']['sources']], ['1'])

    def test_iteration_limit_stops_repeated_tools_without_claiming_success(self):
        model = ScriptedModel(replies=[call('retrieve_classroom', {'query': '重复'}) for _ in range(12)])
        result = build_agent(Backend(), gateway=Gateway(), model=model, max_model_calls=3).invoke('问题', context=self.context, thread_id='x')
        self.assertEqual(len(model._seen), 3)
        self.assertEqual(result['model_call_count'], 3)
        self.assertTrue(result['output']['degraded'])

    def test_evidence_resets_between_turns_in_same_thread(self):
        backend = Backend([Chunk(text='导数是变化率', ref='讲义', score=.9)])
        model = ScriptedModel(replies=[call('retrieve_classroom', {'query': '导数'}),
            call('filter_evidence', {'keep_ids': ['1']}), final('导数是变化率[1]'),
            final('旧证据[1]'), final('暂时无法确认')])
        agent = build_agent(backend, gateway=Gateway(), model=model)
        agent.invoke('导数', context=self.context, thread_id='x')
        result = agent.invoke('新问题', context=self.context, thread_id='x')
        self.assertEqual(result['output']['sources'], [])
        self.assertNotIn('旧证据', result['output']['answer'])

    def test_model_can_search_web_after_classroom_miss(self):
        gateway = Gateway([{'findings': [{'point': '导数是变化率', 'source_title': '教材',
            'source_url': 'https://example.org/book', 'confidence': 'high'}]}],
            search=SearchResult(summary='导数是变化率', sources=[SearchSource(title='教材', url='https://example.org/book')]))
        model = ScriptedModel(replies=[call('retrieve_classroom', {'query': '导数'}),
            call('web_search', {'query': '导数 官方教材'}), call('filter_evidence', {'keep_ids': ['E1']}), final('导数是变化率[E1]')])
        result = build_agent(Backend(), gateway=gateway, model=model).invoke('导数', context=self.context, thread_id='x')
        self.assertEqual(result['output']['mode'], 'external')
        self.assertEqual(result['output']['sources'][0]['url'], 'https://example.org/book')

    def test_tools_do_not_end_the_graph_directly(self):
        from agent.tools import build_tools
        from agent.services import StudyService
        self.assertTrue(all(not tool.return_direct for tool in build_tools(StudyService(Backend(), Gateway()))))

    def test_unknown_evidence_selection_does_not_authorize_citations(self):
        backend = Backend([Chunk(text='导数是变化率', ref='讲义', score=.9)])
        model = ScriptedModel(replies=[call('retrieve_classroom', {'query': '导数'}),
            call('filter_evidence', {'keep_ids': ['99']}), final('伪造[99]'), final('暂时无法确认')])
        result = build_agent(backend, gateway=Gateway(), model=model).invoke('导数', context=self.context, thread_id='x')
        self.assertTrue(result['output']['degraded'])
        self.assertNotIn('伪造', result['output']['answer'])

    def test_search_rechecks_access_before_any_external_call(self):
        class Denied(Backend):
            def material(self, context):
                raise PermissionError('private diagnostic')
        class NoNetwork(Gateway):
            def search(self, question):
                raise AssertionError('External search must not run')
        model = ScriptedModel(replies=[call('web_search', {'query': '问题'}), final('暂时无法确认')])
        result = build_agent(Denied(), gateway=NoNetwork(), model=model).invoke('问题', context=self.context, thread_id='x')
        self.assertNotIn('private', str(result))
        self.assertIn('无权', model._seen[1][-1].content)

    def test_parallel_tool_batch_is_rejected_without_running_search(self):
        backend = Backend([Chunk(text='原文', ref='讲义', score=.9)])
        message = AIMessage(content='', tool_calls=[
            {'name': 'retrieve_classroom', 'args': {'query': '一'}, 'id': 'a', 'type': 'tool_call'},
            {'name': 'web_search', 'args': {'query': '二'}, 'id': 'b', 'type': 'tool_call'}])
        gateway = Gateway()
        model = ScriptedModel(replies=[message, final('暂时无法确认')])
        result = build_agent(backend, gateway=gateway, model=model).invoke('问题', context=self.context, thread_id='x')
        self.assertEqual(gateway.calls, [])
        self.assertEqual(len([m for m in model._seen[1] if m.type == 'tool' and m.status == 'error']), 2)
        self.assertTrue(result['output']['degraded'])

    def test_task_result_cannot_reuse_previous_turn_success(self):
        from test_runtime import task_done
        model = ScriptedModel(replies=[call('get_user_preferences'), task_done(), task_done(), final('暂时无法确认')])
        agent = build_agent(Backend(), gateway=Gateway(), model=model)
        self.assertIn('preferences', agent.invoke('我的偏好', context=self.context, thread_id='x')['output'])
        result = agent.invoke('新问题', context=self.context, thread_id='x')['output']
        self.assertNotIn('preferences', result)

    def test_stream_invalid_draft_is_cleared_before_model_revision(self):
        backend = Backend([Chunk(text='导数是变化率', ref='讲义', score=.9)])
        model = ScriptedModel(replies=[call('retrieve_classroom', {'query': '导数'}),
            call('filter_evidence', {'keep_ids': ['1']}), final('错误[99]'), final('导数是变化率[1]')])
        events = list(build_agent(backend, gateway=Gateway(), model=model).stream('问题', context=self.context, thread_id='x'))
        self.assertTrue(any(e['event'] == 'replace' and e['data']['text'] == '' for e in events))
        self.assertEqual(events[-1]['data']['model_call_count'], 4)
        self.assertNotIn('错误', events[-1]['data']['output']['answer'])
        self.assertEqual(events[-2]['data']['text'], events[-1]['data']['output']['answer'])

    def test_stream_parser_handles_escapes_and_never_exposes_control_fields(self):
        from agent.loop import streamed_answer_prefix
        for text in ('中文\n换行', '带"引号"和\\反斜线', '表情😀'):
            wire = json.dumps({'answer': text, 'followups': ['不应显示']}, ensure_ascii=True)
            previous = ''
            for index in range(len(wire) + 1):
                prefix = streamed_answer_prefix(wire[:index])
                self.assertTrue(prefix.startswith(previous))
                self.assertNotIn('不应显示', prefix)
                previous = prefix
            self.assertEqual(previous, text)
        self.assertEqual(streamed_answer_prefix('中间推理内容'), '')
        self.assertEqual(streamed_answer_prefix('{"task_result":true}'), '')
