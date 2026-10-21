import json
import tempfile
import unittest
import httpx
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.extraction import Extractor
from agent.ingestion import Ingestor
from agent.knowledge import KnowledgeBase
from agent.runtime import build_agent
from agent.schemas import UserContext
from agent.transport import create_router, encode_sse
from agent.gateway import DashScopeGateway, Settings
from agent.media import Embedder, SpeechTranscriber
from test_knowledge import TestEmbedder
from test_runtime import ScriptedModel, call, task_done
from test_agent_loop import final
from test_streaming import StreamGateway


class FullChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.context = UserContext('u', 't', 'n')
        self.path = Path(self.temp.name)
        self.kb = KnowledgeBase(self.path / 'kb.db', TestEmbedder())
        self.kb.register_note(self.context, title='微积分', course='数学')
        lecture = self.path / 'lecture.txt'
        lecture.write_text('[08:15] 导数是变化率。', encoding='utf-8')
        self.asset = self.kb.register_file(self.context, lecture)

    def test_registered_material_tool_to_rag_stream(self):
        model = ScriptedModel(replies=[call('ingest_material', {'asset_id': self.asset}), task_done(),
            call('retrieve_classroom', {'query': '导数是什么'}), call('filter_evidence', {'keep_ids': ['1']}), final('导数是变化率[1]。')])
        gateway = StreamGateway(['导数是', '变化率[1]。'])
        agent = build_agent(self.kb, gateway=gateway, model=model, ingestor=Ingestor(self.kb, Extractor()))
        ingestion = list(agent.stream('导入材料', context=self.context, thread_id='x'))
        self.assertEqual(ingestion[-1]['data']['output']['stage'], 'indexed')
        self.assertTrue(any(e['event'] == 'status' and e['data']['stage'] == 'embedding' for e in ingestion))
        answer = list(agent.stream('导数是什么', context=self.context, thread_id='x'))[-1]['data']
        self.assertEqual(answer['output']['mode'], 'kb')
        self.assertEqual(answer['output']['sources'][0]['timestamp_seconds'], 495)
        self.assertEqual(answer['model_call_count'], 5)

    def test_sse_endpoint_uses_injected_identity_and_returns_complete_events(self):
        list(Ingestor(self.kb, Extractor()).ingest(self.asset, self.context))
        agent = build_agent(self.kb, gateway=StreamGateway(['变化率[1]。']), model=ScriptedModel(replies=[
            call('retrieve_classroom', {'query': '导数'}), call('filter_evidence', {'keep_ids': ['1']}), final('变化率[1]。')]), ingestor=Ingestor(self.kb, Extractor()))
        app = FastAPI()
        app.include_router(create_router(agent, lambda: self.context))
        with TestClient(app) as client:
            rejected = client.post('/agent/stream', json={'message': '问题', 'thread_id': 'x', 'user_id': 'other'})
            self.assertEqual(rejected.status_code, 422)
            response = client.post('/agent/stream', json={'message': '问题', 'thread_id': 'x'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/event-stream', response.headers['content-type'])
        self.assertIn('event: delta\n', response.text)
        self.assertIn('event: done\n', response.text)
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual(frames[-1]['output']['mode'], 'kb')

    def test_sse_encoder_keeps_multiline_text_inside_single_json_frame(self):
        encoded = encode_sse({'event': 'delta', 'data': {'text': '第一行\n第二行'}})
        self.assertEqual(len(encoded.splitlines()), 3)
        self.assertIn('\\n', encoded)

    def test_agent_cannot_import_other_users_asset(self):
        other = UserContext('other', 't', 'n')
        self.kb.register_note(other, title='另一个用户')
        agent = build_agent(self.kb, gateway=StreamGateway([]), model=ScriptedModel(replies=[call('ingest_material', {'asset_id': self.asset}), task_done()]), ingestor=Ingestor(self.kb, Extractor()))
        result = agent.invoke('导入', context=other, thread_id='x')
        self.assertTrue(result['output']['degraded'])
        self.assertEqual(self.kb.material(other).text, '')

    def test_asr_to_real_embedding_adapter_to_persistent_retrieval(self):
        def handler(request):
            url = str(request.url)
            if url.endswith('/embeddings'):
                count = len(json.loads(request.content)['input'])
                return httpx.Response(200, json={'data': [{'index': i, 'embedding': [1.] + [0.] * 1023} for i in range(count)]})
            if request.method == 'POST':
                return httpx.Response(200, json={'output': {'task_id': 'asr1', 'task_status': 'PENDING'}})
            if '/tasks/' in url:
                return httpx.Response(200, json={'output': {'task_status': 'SUCCEEDED', 'results': [{'subtask_status': 'SUCCEEDED', 'transcription_url': 'https://result.oss-cn-beijing.aliyuncs.com/asr.json'}]}})
            return httpx.Response(200, json={'transcripts': [{'sentences': [{'begin_time': 62000, 'end_time': 65000, 'text': '导数表示瞬时变化率'}]}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            gateway = DashScopeGateway(Settings(api_key='offline-test'), client)
            kb = KnowledgeBase(self.path / 'audio.db', Embedder(gateway))
            kb.register_note(self.context, title='录音课')
            asset = kb.register_audio(self.context, 'https://example.org/audio.wav')
            final = list(Ingestor(kb, Extractor(), SpeechTranscriber(gateway, poll_interval=0)).ingest(asset, self.context))[-1]
            self.assertEqual(final['stage'], 'indexed')
            hit = kb.retrieve('导数', self.context)[0]
            self.assertAlmostEqual(hit.score, 1.)
            self.assertIn('01:02', hit.ref)
            self.assertIn('[01:02]', kb.material(self.context).text)
