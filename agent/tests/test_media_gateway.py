import json
import unittest

import httpx

from agent.gateway import DashScopeGateway, Settings
from agent.media import SpeechTranscriber, Embedder


class MediaTests(unittest.TestCase):
    def gateway(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return DashScopeGateway(Settings(api_key='test-only'), client)

    def test_embeddings_restore_index_order_and_batch(self):
        calls = []
        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            count = len(body['input'])
            return httpx.Response(200, json={'data': [{'index': i, 'embedding': [float(i + 1)] + [0.] * 1023} for i in reversed(range(count))]})
        vectors = Embedder(self.gateway(handler)).embed(['a', 'b', 'c'], batch_size=2)
        self.assertEqual([v[0] for v in vectors], [1., 2., 1.])
        self.assertEqual(calls[0]['model'], 'text-embedding-v4')
        self.assertEqual(calls[0]['dimensions'], 1024)
        self.assertEqual(len(calls), 2)

    def test_embeddings_reject_missing_bad_or_zero_vectors(self):
        for vector in ([], [1.], [0.] * 1024, [float('nan')] * 1024):
            # json text permits NaN to test defensive provider parsing.
            gateway = self.gateway(lambda r: httpx.Response(200, text=json.dumps({'data': [{'index': 0, 'embedding': vector}]})))
            with self.assertRaises(ValueError):
                Embedder(gateway).embed(['text'])

    def test_asr_async_submit_poll_and_sentence_timestamps(self):
        seen = []
        def handler(request):
            seen.append(request)
            if request.method == 'POST':
                self.assertEqual(request.headers['X-DashScope-Async'], 'enable')
                self.assertEqual(json.loads(request.content)['model'], 'paraformer-v2')
                return httpx.Response(200, json={'output': {'task_id': 'task_123', 'task_status': 'PENDING'}})
            if '/tasks/' in str(request.url):
                return httpx.Response(200, json={'output': {'task_id': 'task_123', 'task_status': 'SUCCEEDED', 'results': [{'subtask_status': 'SUCCEEDED', 'transcription_url': 'https://results.oss-cn-beijing.aliyuncs.com/t.json'}]}})
            self.assertNotIn('authorization', request.headers)
            return httpx.Response(200, json={'transcripts': [{'channel_id': 0, 'sentences': [{'begin_time': 495000, 'end_time': 498000, 'text': '导数表示变化率。'}]}]})
        events = list(SpeechTranscriber(self.gateway(handler), poll_interval=0).transcribe('https://example.org/lecture.wav'))
        segment = events[-1]['segments'][0]
        self.assertEqual(segment.timestamp_ms, 495000)
        self.assertIn('08:15', segment.ref)
        self.assertEqual(segment.text, '导数表示变化率。')
        self.assertEqual(len(seen), 3)

    def test_asr_failed_job_and_untrusted_result_url_are_rejected(self):
        for status, url in [('FAILED', ''), ('SUCCEEDED', 'http://127.0.0.1/private')]:
            def handler(request):
                if request.method == 'POST':
                    return httpx.Response(200, json={'output': {'task_id': 't', 'task_status': 'PENDING'}})
                return httpx.Response(200, json={'output': {'task_status': status, 'results': [{'subtask_status': 'SUCCEEDED', 'transcription_url': url}]}})
            with self.assertRaises(RuntimeError):
                list(SpeechTranscriber(self.gateway(handler), poll_interval=0).transcribe('https://example.org/a.wav'))

    def test_asr_deadline_stops_polling(self):
        gateway = self.gateway(lambda r: httpx.Response(200, json={'output': {'task_id': 't', 'task_status': 'RUNNING'}}))
        with self.assertRaises(TimeoutError):
            list(SpeechTranscriber(gateway, poll_interval=0, max_polls=2).transcribe('https://example.org/a.wav'))

    def test_asr_rejects_sentence_without_source_time(self):
        def handler(request):
            if request.method == 'POST':
                return httpx.Response(200, json={'output': {'task_id': 't', 'task_status': 'PENDING'}})
            if '/tasks/' in str(request.url):
                return httpx.Response(200, json={'output': {'task_status': 'SUCCEEDED', 'results': [{'subtask_status': 'SUCCEEDED', 'transcription_url': 'https://results.oss-cn-beijing.aliyuncs.com/t.json'}]}})
            return httpx.Response(200, json={'transcripts': [{'sentences': [{'text': '缺少时间戳'}]}]})
        with self.assertRaises(RuntimeError):
            list(SpeechTranscriber(self.gateway(handler), poll_interval=0).transcribe('https://example.org/a.wav'))
