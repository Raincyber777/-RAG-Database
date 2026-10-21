import tempfile
import unittest
from pathlib import Path

from agent.knowledge import KnowledgeBase
from agent.ingestion import Ingestor
from agent.extraction import Extractor
from agent.materials import Segment
from agent.schemas import UserContext


class TestEmbedder:
    model = 'test-embedding'
    dimensions = 2
    signature = 'test-embedding:2'

    def embed(self, texts):
        return [[1., 0.] if '导数' in text else [0., 1.] for text in texts]


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = UserContext('u1', 'tenant', 'note')
        self.kb = KnowledgeBase(self.root / 'knowledge.db', TestEmbedder())
        self.kb.register_note(self.context, title='微积分', course='数学')

    def asset(self, text):
        path = self.root / 'lecture.txt'
        path.write_text(text, encoding='utf-8')
        return self.kb.register_file(self.context, path)

    def test_ingest_retrieve_and_reopen_persists_cosine_and_sources(self):
        asset = self.asset('[08:15] 导数表示变化率。\n积分表示累计。')
        result = list(Ingestor(self.kb, Extractor()).ingest(asset, self.context))[-1]
        self.assertEqual(result['stage'], 'indexed')
        reopened = KnowledgeBase(self.root / 'knowledge.db', TestEmbedder())
        hits = reopened.retrieve('导数是什么', self.context)
        self.assertAlmostEqual(hits[0].score, 1.)
        self.assertIn('08:15', hits[0].ref)
        self.assertIn('[08:15]', reopened.material(self.context).text)

    def test_asset_and_note_are_scoped_before_any_file_or_embedding_access(self):
        asset = self.asset('导数')
        other = UserContext('other', 'tenant', 'note')
        self.kb.register_note(other, title='其他用户', course='数学')
        with self.assertRaises(PermissionError):
            list(Ingestor(self.kb, Extractor()).ingest(asset, other))
        with self.assertRaises(PermissionError):
            self.kb.material(UserContext('u1', 'other-tenant', 'note'))
        self.assertEqual(self.kb.retrieve('导数', other), [])

    def test_reimport_replaces_same_asset_and_failure_keeps_previous_version(self):
        asset = self.asset('导数旧版本')
        ingestor = Ingestor(self.kb, Extractor())
        list(ingestor.ingest(asset, self.context))
        (self.root / 'lecture.txt').write_text('导数新版本', encoding='utf-8')
        list(ingestor.ingest(asset, self.context))
        self.assertEqual(len(self.kb.retrieve('导数', self.context)), 1)
        self.assertNotIn('旧版本', self.kb.material(self.context).text)
        (self.root / 'lecture.txt').write_text('坏版本', encoding='utf-8')
        class FailingEmbedder(TestEmbedder):
            def embed(self, texts):
                raise RuntimeError('provider unavailable')
        self.kb.embedder = FailingEmbedder()
        with self.assertRaises(RuntimeError):
            list(ingestor.ingest(asset, self.context))
        self.assertIn('新版本', self.kb.material(self.context).text)

    def test_changed_embedding_model_rejected_on_existing_index(self):
        class Different(TestEmbedder):
            signature = 'different:2'
        with self.assertRaises(ValueError):
            KnowledgeBase(self.root / 'knowledge.db', Different())

    def test_partial_vector_batch_cannot_replace_existing_data(self):
        asset = self.asset('导数')
        list(Ingestor(self.kb, Extractor()).ingest(asset, self.context))
        with self.assertRaises(ValueError):
            self.kb.replace_source(self.context, asset, [Segment(text='新内容', ref='p1')], [])
        self.assertIn('导数', self.kb.material(self.context).text)

    def test_material_uses_original_segments_without_overlap_duplication(self):
        original = '导数' + '甲乙丙丁' * 600 + '结尾'
        asset = self.asset(original)
        list(Ingestor(self.kb, Extractor()).ingest(asset, self.context))
        text = self.kb.material(self.context).text
        self.assertEqual(text.count('甲乙丙丁'), 600)
        self.assertEqual(text.count('lecture.txt'), 1)
