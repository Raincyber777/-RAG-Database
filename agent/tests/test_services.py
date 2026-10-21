import json
import unittest
from collections import deque

from agent.schemas import Chunk, Material, SearchResult, SearchSource, UserContext
from agent.services import StudyService, select_mode, validate_notes, validate_quiz


class Backend:
    def __init__(self, chunks=(), text='[08:15] 导数描述函数的瞬时变化率。'):
        self.chunks = list(chunks)
        self.text = text

    def material(self, context):
        return Material(title='微积分', course='数学', text=self.text)

    def retrieve(self, question, context, limit=4):
        return self.chunks[:limit]

    def profile(self, context):
        return {'name': '同学'}


class Gateway:
    def __init__(self, replies=(), search=None):
        self.replies = deque(replies)
        self.search_result = search
        self.calls = []

    def chat_json(self, system, user, **kwargs):
        self.calls.append((system, user, kwargs))
        return self.replies.popleft() if self.replies else None

    def search(self, question):
        return self.search_result


CTX = UserContext(user_id='u1', tenant_id='t1', note_id='n1')


class AnswerTests(unittest.TestCase):
    def test_cosine_boundaries(self):
        for score, expected in [(0.42, 'kb'), (0.419, 'fusion'), (0.22, 'fusion'), (0.219, 'external')]:
            with self.subTest(score=score):
                self.assertEqual(select_mode([Chunk(text='原文', ref='讲义', score=score)]), expected)
        self.assertEqual(select_mode([]), 'external')

    def test_no_evidence_skips_answer_model(self):
        gateway = Gateway()
        result = StudyService(Backend(), gateway).answer('什么是导数？', CTX)
        self.assertIn('暂时无法确认', result['answer'])
        self.assertEqual(result['sources'], [])
        self.assertEqual(gateway.calls, [])

    def test_kb_citation_maps_timestamp(self):
        gateway = Gateway([{'answer': '导数描述瞬时变化率。[1]', 'followups': ['导数如何计算？']}])
        result = StudyService(Backend([Chunk(text='导数描述瞬时变化率。', ref='录音 08:15', score=.7)]), gateway).answer('什么是导数？', CTX)
        self.assertEqual(result['sources'][0]['timestamp_seconds'], 495)
        self.assertEqual(result['mode'], 'kb')
        self.assertFalse(result['degraded'])

    def test_forged_citation_falls_back_to_actual_excerpt(self):
        gateway = Gateway([{'answer': '虚构结论[99]', 'followups': []}])
        result = StudyService(Backend([Chunk(text='真实材料', ref='讲义', score=.7)]), gateway).answer('问题', CTX)
        self.assertNotIn('虚构结论', result['answer'])
        self.assertIn('真实材料', result['answer'])
        self.assertTrue(result['degraded'])

    def test_kb_uncited_claim_is_rejected(self):
        gateway = Gateway([{'answer': '依据材料[1]。这里是无引用断言。', 'followups': []}])
        result = StudyService(Backend([Chunk(text='真实原文', ref='讲义', score=.7)]), gateway).answer('问题', CTX)
        self.assertTrue(result['degraded'])
        self.assertNotIn('无引用断言', result['answer'])

    def test_external_whitelist_and_annotation(self):
        search = SearchResult(summary='真实摘要', sources=[SearchSource(title='真实来源', url='https://example.org/doc', site_name='文档')])
        gateway = Gateway([
            {'findings': [
                {'point': '虚构', 'source_title': '伪造', 'source_url': 'https://fake.org', 'confidence': 'high'},
                {'point': '真实要点', 'source_title': '模型篡改标题', 'source_url': 'https://example.org/doc', 'confidence': 'high'},
            ]},
            {'answer': '真实要点[E1]。另一个说明[E1]。', 'followups': []},
        ], search)
        result = StudyService(Backend(), gateway).answer('问题', CTX)
        self.assertEqual(result['mode'], 'external')
        self.assertEqual(result['answer'].count('补充自外部知识'), 2)
        self.assertEqual(result['sources'][0]['title'], '真实来源')
        self.assertNotIn('fake.org', str(result))

    def test_low_confidence_only_skips_answer_generation(self):
        gateway = Gateway([{'findings': [{'point': '说法', 'source_title': '论坛', 'source_url': 'https://example.org', 'confidence': 'low'}]}], SearchResult(summary='摘要', sources=[SearchSource(title='论坛', url='https://example.org')]))
        result = StudyService(Backend(), gateway).answer('问题', CTX)
        self.assertIn('暂时无法确认', result['answer'])
        self.assertEqual(len(gateway.calls), 1)

    def test_failed_search_keeps_weak_kb(self):
        result = StudyService(Backend([Chunk(text='课堂相关内容', ref='讲义', score=.3)]), Gateway()).answer('问题', CTX)
        self.assertEqual(result['mode'], 'kb')
        self.assertIn('课堂相关内容', result['answer'])

    def test_bracket_numbers_inside_fallback_evidence_do_not_become_citations(self):
        result = StudyService(Backend([Chunk(text='材料原句带 [999] 和 [E8]', ref='讲义', score=.6)]), Gateway()).answer('问题', CTX)
        self.assertEqual([s['id'] for s in result['sources']], ['1'])

    def test_external_marker_is_not_duplicated(self):
        search = SearchResult(summary='摘要', sources=[SearchSource(title='文档', url='https://example.org')])
        gateway = Gateway([
            {'findings': [{'point': '要点', 'source_title': '文档', 'source_url': 'https://example.org', 'confidence': 'high'}]},
            {'answer': '要点[E1]，补充自外部知识。', 'followups': []},
        ], search)
        result = StudyService(Backend(), gateway).answer('问题', CTX)
        self.assertEqual(result['answer'].count('补充自外部知识'), 1)

    def test_uncited_external_sentence_rejects_entire_answer(self):
        search = SearchResult(summary='摘要', sources=[SearchSource(title='文档', url='https://example.org')])
        gateway = Gateway([
            {'findings': [{'point': '可信内容', 'source_title': '文档', 'source_url': 'https://example.org', 'confidence': 'high'}]},
            {'answer': '可信内容[E1]。凭空编造的第二句话。', 'followups': []},
        ], search)
        result = StudyService(Backend(), gateway).answer('问题', CTX)
        self.assertTrue(result['degraded'])
        self.assertNotIn('凭空编造', result['answer'])


class StructuredTests(unittest.TestCase):
    def test_timestamp_must_exist_at_material_line_start(self):
        data = {'theme': '导数', 'points': [{'text': '变化率', 'ts': '09:00'}, {'text': '定义', 'ts': '08:15'}], 'definitions': [], 'emphasized': []}
        result = validate_notes(data, '[08:15] 原文提到 09:00，但不是来源时间。')
        self.assertNotIn('ts', result['points'][0])
        self.assertEqual(result['points'][1]['ts'], '08:15')

    def test_extra_json_fields_are_rejected(self):
        self.assertIsNone(validate_notes({'theme': '主题', 'points': [], 'definitions': [], 'emphasized': [], 'unexpected': 1}, '原文'))

    def test_quiz_filters_invalid_answers_citations_and_duplicates(self):
        base = {'type': 'single', 'difficulty': '中等', 'stem': '导数是什么？', 'options': ['A. 变化率', 'B. 常数', 'C. 面积', 'D. 长度'], 'answer': 'A', 'analysis': '依据原文', 'cite': '录音 08:15', 'knowledge': '导数'}
        data = {'questions': [base, base, dict(base, stem='错题一', answer='AB'), dict(base, stem='错题二', cite='录音 09:00'), dict(base, stem='错题三', type='judge', answer='是')]}
        questions = validate_quiz(data, '[08:15] 导数是变化率。（录音 08:15）', 5, [])
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]['answer'], 'A')

    def test_all_six_question_types(self):
        rows = []
        for kind, answer in [('single', 'A'), ('multiple', 'AC'), ('indefinite', 'B'), ('judge', '对'), ('blank', '变化率'), ('answer', '函数变化率')]:
            rows.append({'type': kind, 'difficulty': '中等', 'stem': kind + (' ________' if kind == 'blank' else '题干'), 'options': ['A. 一', 'B. 二', 'C. 三', 'D. 四'] if kind in ('single', 'multiple', 'indefinite') else [], 'answer': answer, 'analysis': '原文依据', 'cite': '讲义', 'knowledge': '导数'})
        self.assertEqual(len(validate_quiz({'questions': rows}, '讲义：导数表示变化率。', 6, [])), 6)

    def test_empty_material_does_not_generate_quiz(self):
        gateway = Gateway()
        result = StudyService(Backend(text=''), gateway).quiz(CTX, 4, [])
        self.assertEqual(result['questions'], [])
        self.assertEqual(result['missing_count'], 4)
        self.assertEqual(gateway.calls, [])

    def test_summary_fallback_extracts_original_text(self):
        result = StudyService(Backend(), Gateway()).summarize(CTX)
        self.assertTrue(result['degraded'])
        self.assertEqual(result['points'][0]['ts'], '08:15')
        self.assertIn('导数', result['points'][0]['text'])

    def test_suggestions_filter_duplicates_history_and_length(self):
        gateway = Gateway([{'suggestions': ['导数是什么？', '导数如何计算？', '导数如何计算？', '积分如何理解？', '长' * 19]}])
        result = StudyService(Backend(), gateway).suggest(CTX, ['导数是什么？'])
        self.assertEqual(result['suggestions'], ['导数如何计算？', '积分如何理解？'])

    def test_long_summary_invalid_reduce_preserves_map_output_and_flags_fallback(self):
        material = '[08:15] ' + '导数描述变化率。' * 1600 + '\n[09:00] ' + '积分累计变化。' * 1600
        one = {'theme': '导数', 'points': [{'text': '导数描述变化率', 'ts': '08:15'}], 'definitions': [], 'emphasized': []}
        two = {'theme': '积分', 'points': [{'text': '积分累计变化', 'ts': '09:00'}], 'definitions': [], 'emphasized': []}
        gateway = Gateway([one, one, two, {'invalid': 'reduce'}])
        result = StudyService(Backend(text=material), gateway).summarize(CTX)
        self.assertTrue(result['degraded'])
        self.assertGreater(result['parts'], 1)
        self.assertTrue(any(p['text'] == '导数描述变化率' for p in result['points']))
        self.assertTrue(all(len(json.loads(call[1])['text']) <= 12000 for call in gateway.calls[:-1]))

    def test_invalid_choice_option_duplicate_content_is_dropped(self):
        row = {'type': 'single', 'difficulty': '中等', 'stem': '题干', 'options': ['A. 相同', 'B. 相同', 'C. 三', 'D. 四'], 'answer': 'A', 'analysis': '原文', 'cite': '讲义', 'knowledge': '导数'}
        self.assertEqual(validate_quiz({'questions': [row]}, '讲义', 1, []), [])


if __name__ == '__main__':
    unittest.main()
