import json
import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx

from agent.gateway import DashScopeGateway, Settings


class GatewayTests(unittest.TestCase):
    def gateway(self, handler, **settings):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return DashScopeGateway(Settings(api_key='test-only', **settings), client)

    def test_json_mode_and_fast_model_request(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '{"followups": ["怎么计算？"]}'}, 'finish_reason': 'stop'}]})
        result = self.gateway(handler).chat_json('规则', '问题', model='fast', temperature=.7)
        self.assertEqual(result, {'followups': ['怎么计算？']})
        self.assertEqual(requests[0]['model'], 'qwen-flash')
        self.assertEqual(requests[0]['response_format'], {'type': 'json_object'})

    def test_invalid_json_and_timeout_are_not_retried(self):
        for mode in ('json', 'timeout', 'length'):
            calls = []
            def handler(request):
                calls.append(request)
                if mode == 'timeout':
                    raise httpx.ReadTimeout('timeout', request=request)
                return httpx.Response(200, json={'choices': [{'message': {'content': 'not json'}, 'finish_reason': 'length' if mode == 'length' else 'stop'}]})
            self.assertIsNone(self.gateway(handler).chat_json('规则', '问题'))
            self.assertEqual(len(calls), 1)

    def test_missing_key_does_not_send_request(self):
        def handler(request):
            self.fail('No HTTP request should be sent without a key')
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        gateway = DashScopeGateway(Settings(), client)
        self.assertIsNone(gateway.chat_json('规则', '问题'))
        self.assertIsNone(gateway.search('问题'))

    def test_search_uses_native_sources_not_model_generated_urls(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'output': {'choices': [{'message': {'role': 'assistant', 'content': '摘要中的 https://fake.org 不算来源'}}], 'search_info': {'search_results': [{'title': '文档', 'url': 'https://example.org'}, {'title': '错误链接', 'url': 'javascript:alert(1)'}]}}})
        result = self.gateway(handler, search_provider='dashscope').search('问题')
        self.assertEqual([s.url for s in result.sources], ['https://example.org'])
        self.assertTrue(requests[0]['parameters']['enable_search'])
        self.assertTrue(requests[0]['parameters']['search_options']['enable_source'])

    def test_vision_uses_multimodal_model_and_rejects_local_paths(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '板书：导数'}, 'finish_reason': 'stop'}]})
        gateway = self.gateway(handler)
        self.assertEqual(gateway.vision('https://example.org/board.png'), '板书：导数')
        self.assertEqual(requests[0]['model'], 'qwen3-vl-plus')
        self.assertEqual(requests[0]['messages'][0]['content'][0]['type'], 'image_url')
        with self.assertRaises(ValueError):
            gateway.vision('file:///private.png')

    def test_search_without_native_source_list_returns_none(self):
        gateway = self.gateway(lambda request: httpx.Response(200, json={'output': {'choices': [{'message': {'content': '请相信 https://fake.org'}}]}}), search_provider='dashscope')
        self.assertIsNone(gateway.search('问题'))

    def test_tavily_is_default_and_uses_its_own_key_and_result_content(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={'answer': '未采用摘要 https://fake.org', 'results': [
                {'title': '导数', 'url': 'https://example.org/derivative', 'content': '导数表示瞬时变化率'},
                {'title': '重复', 'url': 'https://example.org/derivative', 'content': '重复内容'},
                {'title': '无正文', 'url': 'https://empty.org', 'content': ''},
                {'title': '无效', 'url': 'javascript:alert(1)', 'content': '无效正文'},
            ]})
        gateway = self.gateway(handler, tavily_api_key='tavily-test')
        result = gateway.search('什么是导数？')
        self.assertEqual(str(requests[0].url), 'https://api.tavily.com/search')
        self.assertEqual(requests[0].headers['Authorization'], 'Bearer tavily-test')
        body = json.loads(requests[0].content)
        self.assertEqual(body['query'], '什么是导数？')
        self.assertEqual(body['max_results'], 5)
        self.assertEqual(body['search_depth'], 'basic')
        self.assertFalse(body['include_answer'])
        self.assertNotIn('test-only', requests[0].content.decode())
        self.assertEqual([s.url for s in result.sources], ['https://example.org/derivative'])
        self.assertIn('导数表示瞬时变化率', result.summary)
        self.assertNotIn('fake.org', result.summary)
        self.assertNotIn('重复内容', result.summary)

    def test_tavily_works_without_dashscope_key(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            'results': [{'url': 'https://example.org', 'content': '正文'}],
        })))
        self.addCleanup(client.close)
        result = DashScopeGateway(Settings(tavily_api_key='test'), client).search('问题')
        self.assertEqual(result.sources[0].title, 'https://example.org')

    def test_tavily_missing_key_or_disabled_never_falls_back_silently(self):
        def handler(request):
            self.fail('No search request expected')
        for config in ({}, {'tavily_api_key': 'test', 'enable_search': False}):
            self.assertIsNone(self.gateway(handler, **config).search('问题'))

    def test_tavily_failure_and_malformed_results_degrade_without_retry(self):
        for value in ('timeout', 'unauthorized', 'invalid-json', [], {'results': None},
                      {'results': [None, {}, {'url': 'https://example.org', 'content': 123}]}):
            with self.subTest(value=value):
                calls = []
                def handler(request):
                    calls.append(request)
                    if value == 'timeout':
                        raise httpx.ReadTimeout('private diagnostics', request=request)
                    if value == 'unauthorized':
                        return httpx.Response(401, json={'detail': 'private diagnostics'})
                    if value == 'invalid-json':
                        return httpx.Response(200, text='not json')
                    return httpx.Response(200, json=value)
                self.assertIsNone(self.gateway(handler, tavily_api_key='test').search('问题'))
                self.assertEqual(len(calls), 1)

    def test_settings_read_dotenv_without_overriding_process_or_exposing_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / '.env'
            env.write_text('TAVILY_API_KEY=file-secret\nDASHSCOPE_API_KEY=model-secret\n', encoding='utf-8')
            with patch.dict(os.environ, {}, clear=True):
                config = Settings.from_env(env_file=env)
                self.assertEqual(config.tavily_api_key, 'file-secret')
                self.assertEqual(config.api_key, 'model-secret')
                self.assertEqual(config.search_provider, 'tavily')
                self.assertNotIn('secret', repr(config))
                self.assertNotIn('TAVILY_API_KEY', os.environ)
            with patch.dict(os.environ, {'TAVILY_API_KEY': 'process-secret'}, clear=True):
                self.assertEqual(Settings.from_env(env_file=env).tavily_api_key, 'process-secret')

    def test_unknown_search_provider_is_rejected(self):
        with self.assertRaises(ValueError):
            Settings(search_provider='typo')

    def test_tavily_results_flow_through_answer_source_validation(self):
        from agent.backend import EmptyBackend
        from agent.schemas import UserContext
        from agent.services import StudyService
        calls = []
        def handler(request):
            calls.append(request.url.host)
            if request.url.host == 'api.tavily.com':
                return httpx.Response(200, json={'results': [
                    {'title': '官方文档', 'url': 'https://docs.python.org/3/', 'content': 'Python 支持列表推导式。'},
                ]})
            if len(calls) == 2:
                content = {'findings': [
                    {'point': 'Python 支持列表推导式', 'source_title': '模型标题',
                     'source_url': 'https://docs.python.org/3/', 'confidence': 'high'},
                    {'point': '虚构结论', 'source_title': '伪造来源',
                     'source_url': 'https://fake.org', 'confidence': 'high'},
                ]}
            else:
                content = {'answer': 'Python 支持列表推导式[E1]', 'followups': []}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content)}, 'finish_reason': 'stop'}]})
        result = StudyService(EmptyBackend(), self.gateway(handler, tavily_api_key='test')).answer(
            'Python 支持列表推导式吗？', UserContext('u', 't'))
        self.assertEqual(calls, ['api.tavily.com', 'dashscope.aliyuncs.com', 'dashscope.aliyuncs.com'])
        self.assertEqual(result['mode'], 'external')
        self.assertFalse(result['degraded'])
        self.assertEqual(result['sources'][0]['title'], '官方文档')
        self.assertNotIn('fake.org', str(result))
        self.assertNotIn('虚构', str(result))


if __name__ == '__main__':
    unittest.main()
