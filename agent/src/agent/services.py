"""Grounded task execution, independent of the agent's routing model."""

import json
import re
from contextlib import closing

from pydantic import ValidationError

from . import prompts
from .gateway import web_url
from .schemas import Answer, Finding, Notes, Question, QUESTION_TYPES

TS = r'\d{2,}:[0-5]\d'
CITATION = re.compile(r'\[(E?\d+)\]')
EXTERNAL_PREFIX = '该问题在当前课堂纪要中未找到直接依据，以下为外部知识补充：\n\n'


def select_mode(chunks):
    score = max((c.score for c in chunks), default=-1)
    return 'kb' if score >= .42 else 'fusion' if score >= .22 else 'external'


def _json(data):
    return json.dumps(data, ensure_ascii=False)


def _short_questions(values, limit, max_length, excluded=()):
    seen = {re.sub(r'[\s？?！!。]', '', q) for q in excluded}
    result = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, str):
            continue
        value = value.strip()
        key = re.sub(r'[\s？?！!。]', '', value)
        if 2 <= len(value) <= max_length and key not in seen:
            result.append(value)
            seen.add(key)
        if len(result) == limit:
            break
    return result


def validate_notes(data, material):
    try:
        result = Notes.model_validate(data).model_dump(exclude_none=True)
    except ValidationError:
        return None
    timestamps = set(re.findall(r'^\s*\[(' + TS + r')\]', material, re.M))
    for field in ('points', 'definitions'):
        seen = set()
        rows = []
        for row in result[field]:
            if row.get('ts') not in timestamps:
                row.pop('ts', None)
            if row['text'] not in seen:
                seen.add(row['text'])
                rows.append(row)
        result[field] = rows
    return result


def validate_quiz(data, material, count, kinds):
    if not isinstance(data, dict) or set(data) != {'questions'} or not isinstance(data['questions'], list):
        return []
    questions, seen = [], set()
    for item in data['questions']:
        try:
            q = Question.model_validate(item)
        except ValidationError:
            continue
        key = re.sub(r'\s+', '', q.stem)
        if key in seen or (kinds and q.type not in kinds) or q.cite not in material:
            continue
        if q.type in ('single', 'multiple', 'indefinite'):
            if len(q.options) != 4 or any(not option.startswith(f'{letter}. ') or not option[3:].strip() for letter, option in zip('ABCD', q.options)):
                continue
            if len({option[3:].strip() for option in q.options}) != 4:
                continue
            if not re.fullmatch('[A-D]+', q.answer) or ''.join(sorted(set(q.answer))) != q.answer:
                continue
            if q.type == 'single' and len(q.answer) != 1:
                continue
            if q.type == 'multiple' and len(q.answer) < 2:
                continue
        elif q.options:
            continue
        if q.type == 'judge' and q.answer not in ('对', '错'):
            continue
        if q.type == 'blank' and '________' not in q.stem:
            continue
        seen.add(key)
        questions.append(q.model_dump())
        if len(questions) >= count:
            break
    return questions


def _sources(answer, chunks, findings):
    result, seen = [], set()
    for label in CITATION.findall(answer):
        if label in seen:
            continue
        seen.add(label)
        if label.startswith('E'):
            f = findings[int(label[1:]) - 1]
            result.append({'id': label, 'kind': 'external', 'title': f.source_title, 'url': f.source_url, 'confidence': f.confidence})
        else:
            chunk = chunks[int(label) - 1]
            source = {'id': label, 'kind': 'classroom', 'title': chunk.ref, 'text': chunk.text}
            ts = re.search(TS, chunk.ref)
            if ts:
                minutes, seconds = map(int, ts[0].split(':'))
                source['timestamp_seconds'] = minutes * 60 + seconds
            result.append(source)
    return result


def _validated_answer(text, chunks, findings):
    labels = CITATION.findall(text)
    allowed = {str(i) for i in range(1, len(chunks) + 1)} | {f'E{i}' for i in range(1, len(findings) + 1)}
    if any(label not in allowed for label in labels):
        return None
    if (not labels and '暂时无法确认' not in text) or re.search(r'https?://|www\.', text) or '来源说明' in text:
        return None
    # Accept citations placed just after sentence punctuation, then inspect clauses.
    text = re.sub(r'([。！？!?])\s*((?:\[E?\d+\])+)', r'\2\1', text)
    for sentence in re.split(r'[。！？!?\n]', text):
        if re.search(r'[\w\u4e00-\u9fff]', sentence) and not CITATION.search(sentence) and '暂时无法确认' not in sentence:
            return None
    # Each cited external clause gets a label even when the model forgot it.
    text = re.sub(r'(\[E\d+\])(?:[，, ]*)(?:[（(]?补充自外部知识[）)]?)?', r'\1（补充自外部知识）', text)
    return text


def _quote_evidence(text):
    """Distinguish citation-looking content from our generated reference IDs."""
    return CITATION.sub(r'［\1］', text)


def _excerpt_notes(material):
    points = []
    for line in material.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r'^\[(' + TS + r')\]\s*(.*)', line)
        point = {'text': (match[2] if match else line)[:500]}
        if match:
            point['ts'] = match[1]
        if point['text']:
            points.append(point)
        if len(points) == 8:
            break
    return {'theme': '课堂材料摘录（规则降级）', 'points': points, 'definitions': [], 'emphasized': []}


def _split_material(text, limit=12000):
    """Prefer line boundaries; retain a long line's real timestamp on splits."""
    parts, current = [], ''
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= limit:
            current += line
            continue
        if current:
            parts.append(current)
            current = ''
        ts = re.match(r'^\[(' + TS + r')\]\s*', line)
        prefix = ts[0] if ts else ''
        while len(line) > limit:
            parts.append(line[:limit])
            line = prefix + line[limit:]
        current = line
    if current:
        parts.append(current)
    return parts


class StudyService:
    def __init__(self, backend, gateway, ingestor=None):
        self.backend = backend
        self.gateway = gateway
        self.ingestor = ingestor

    def _findings(self, question):
        search = self.gateway.search(question)
        if search is None:
            return []
        data = self.gateway.chat_json(prompts.FINDINGS_SYSTEM, _json({'question': question, 'summary': search.summary, 'sources': [s.model_dump() for s in search.sources]}), temperature=.2)
        if not isinstance(data, dict) or set(data) != {'findings'} or not isinstance(data['findings'], list):
            return []
        whitelist = {s.url: s for s in search.sources if web_url(s.url)}
        findings, seen = [], set()
        for item in data['findings']:
            try:
                finding = Finding.model_validate(item)
            except ValidationError:
                continue
            if finding.source_url not in whitelist or finding.confidence == 'low' or finding.point.strip() in seen:
                continue
            finding.source_title = whitelist[finding.source_url].title
            seen.add(finding.point.strip())
            findings.append(finding)
        return sorted(findings, key=lambda f: {'high': 0, 'mid': 1}[f.confidence])[:4]

    def _prepare_answer(self, question, context, preferences=None):
        if not question.strip():
            raise ValueError('问题不能为空')
        material = self.backend.material(context)  # Authorize before any external call.
        chunks = sorted(self.backend.retrieve(question, context, limit=4), key=lambda c: c.score, reverse=True)[:4]
        mode = select_mode(chunks)
        findings = self._findings(question) if mode != 'kb' else []
        if mode == 'external':
            chunks = []  # Weakly related hits cannot become answer evidence.
        elif not findings:
            mode = 'kb'
        if not chunks and not findings:
            return {'answer': f'暂时无法确认：当前课堂纪要《{material.title}》中没有找到与该问题相关的内容，外部检索也未获得可靠依据。建议先在工作台上传相关课件或讲义，再重新提问。', 'followups': [], 'sources': [], 'mode': 'unavailable', 'degraded': True}, None
        rules = {'kb': prompts.KB_RULES, 'fusion': prompts.FUSION_RULES, 'external': prompts.EXTERNAL_RULES}[mode]
        user = _json({'title': material.title, 'course': material.course, 'question': question,
                      'preferences': preferences or {},
                      'classroom': [{'id': str(i), 'source': c.ref, 'text': c.text} for i, c in enumerate(chunks, 1)],
                      'external': [{'id': f'E{i}', **f.model_dump()} for i, f in enumerate(findings, 1)]})
        return None, (mode, chunks, findings, rules, user)

    def answer(self, question, context, preferences=None):
        unavailable, prepared = self._prepare_answer(question, context, preferences)
        if unavailable:
            return unavailable
        mode, chunks, findings, rules, user = prepared
        data = self.gateway.chat_json(rules + prompts.JSON_OUT + prompts.DATA_RULES, user, temperature=.3)
        degraded = False
        try:
            parsed = Answer.model_validate(data)
            answer = _validated_answer(parsed.answer, chunks, findings)
            followups = _short_questions(parsed.followups, 3, 16, [question])
        except ValidationError:
            answer, followups = None, []
        return self._finish_answer(answer, followups, mode, chunks, findings)

    @staticmethod
    def _finish_answer(answer, followups, mode, chunks, findings):
        degraded = False
        if answer is None:
            degraded = True
            followups = []
            if chunks:
                answer = '暂时无法确认完整答案，以下为相关课堂材料摘录：\n' + '\n'.join(f'- {_quote_evidence(c.text)} [{i}]' for i, c in enumerate(chunks, 1))
            else:
                answer = '\n'.join(f'- {_quote_evidence(f.point)} [E{i}]（补充自外部知识）' for i, f in enumerate(findings, 1))
        # Sources come from trusted identifiers, never titles or links generated by the LLM.
        sources = _sources(answer, chunks, findings)
        if mode == 'external':
            answer = EXTERNAL_PREFIX + answer
        if sources:
            answer += '\n\n—— 来源说明 ——\n' + '\n'.join(f"[{s['id']}] {s['title']}" + (f"：{s['url']}" if s.get('url') else '') for s in sources)
        return {'answer': answer, 'followups': followups, 'sources': sources, 'mode': mode, 'degraded': degraded}

    def answer_stream(self, question, context, preferences=None):
        """Deltas are provisional. `replace` and `done` contain validated output."""
        yield {'event': 'status', 'data': {'stage': 'retrieving'}}
        unavailable, prepared = self._prepare_answer(question, context, preferences)
        if unavailable:
            result = unavailable
        else:
            mode, chunks, findings, rules, user = prepared
            text, valid = '', None
            yield {'event': 'status', 'data': {'stage': 'generating', 'mode': mode}}
            try:
                with closing(self.gateway.stream_text(rules + prompts.TEXT_OUT + prompts.DATA_RULES, user, temperature=.3)) as stream:
                    for delta in stream:
                        text += delta
                        if len(text) > 32000:
                            raise ValueError('回答长度超限')
                        yield {'event': 'delta', 'data': {'text': delta, 'provisional': True}}
                valid = _validated_answer(text, chunks, findings) if text.strip() else None
            except Exception:
                # A partial response must be replaced, never silently accepted.
                valid = None
            result = self._finish_answer(valid, [], mode, chunks, findings)
        yield {'event': 'replace', 'data': {'text': result['answer'], 'degraded': result['degraded']}}
        yield {'event': 'sources', 'data': {'sources': result['sources']}}
        if not result['degraded']:
            try:
                result['followups'] = self.followups(question, result['answer'])['followups']
            except Exception:
                result['followups'] = []
        yield {'event': 'followups', 'data': {'followups': result['followups']}}
        yield {'event': 'done', 'data': result}

    def summarize(self, context):
        material = self.backend.material(context)
        if not material.text.strip():
            return {**_excerpt_notes(''), 'degraded': True, 'reason': '当前纪要没有可用材料'}
        if len(material.text) <= 15000:
            data = self.gateway.chat_json(prompts.SUMMARY_SYSTEM, _json(material.model_dump()), temperature=.3)
            notes = validate_notes(data, material.text)
            return {**(notes or _excerpt_notes(material.text)), 'degraded': notes is None}
        candidates, degraded = [], False
        parts = _split_material(material.text)
        for i, part in enumerate(parts, 1):
            data = self.gateway.chat_json(prompts.MAP_SYSTEM, _json({'part': i, 'total': len(parts), 'text': part}), temperature=.2)
            notes = validate_notes(data, part)
            degraded |= notes is None
            candidates.append(notes or _excerpt_notes(part))
        merged = {'theme': material.title, 'points': [], 'definitions': [], 'emphasized': []}
        for field in ('points', 'definitions', 'emphasized'):
            seen = set()
            for candidate in candidates:
                for item in candidate[field]:
                    key = _json(item)
                    if key not in seen:
                        seen.add(key)
                        merged[field].append(item)
        # Bound reduce input; every original chunk was processed before this selection.
        budgeted = {'theme': merged['theme']}
        for field, limit in [('points', 64), ('definitions', 32), ('emphasized', 32)]:
            budgeted[field] = merged[field][:limit]
        data = self.gateway.chat_json(prompts.REDUCE_SYSTEM, _json(budgeted), temperature=.2)
        candidate_ts = '\n'.join(f"[{p['ts']}]" for field in ('points', 'definitions') for p in budgeted[field] if p.get('ts'))
        notes = validate_notes(data, candidate_ts)
        degraded |= notes is None
        if notes is None:
            notes = {**merged, 'points': merged['points'][:8], 'definitions': merged['definitions'][:6], 'emphasized': merged['emphasized'][:4]}
        return {**notes, 'degraded': degraded, 'parts': len(parts)}

    def quiz(self, context, count=5, kinds=()):
        if not 1 <= count <= 20 or any(kind not in QUESTION_TYPES for kind in kinds):
            raise ValueError('题数须为 1~20，题型须为支持的六类之一')
        material = self.backend.material(context)
        if not material.text.strip():
            return {'questions': [], 'missing_count': count, 'degraded': True, 'reason': '当前纪要没有可用材料'}
        # Make the exact evidence window explicit; never validate against unseen material.
        text = material.text[:15000]
        data = self.gateway.chat_json(prompts.QUIZ_SYSTEM, _json({'material': text, 'count': count, 'types': list(kinds) or list(QUESTION_TYPES)}), temperature=.5)
        questions = validate_quiz(data, text, count, kinds)
        return {'questions': questions, 'missing_count': count - len(questions), 'degraded': len(questions) < count, 'material_truncated': len(text) < len(material.text)}

    def suggest(self, context, history):
        material = self.backend.material(context)
        recent = [q for q in history[-6:] if isinstance(q, str) and q.strip()]
        if not material.text.strip():
            return {'suggestions': [], 'degraded': True}
        data = self.gateway.chat_json(prompts.SUGGEST_SYSTEM, _json({'title': material.title, 'course': material.course, 'material': material.text[:6000], 'history': recent}), model='fast', temperature=.8)
        values = _short_questions(data.get('suggestions'), 4, 18, recent) if isinstance(data, dict) and set(data) == {'suggestions'} else []
        valid = len(values) >= 2
        return {'suggestions': values if valid else [], 'degraded': not valid}

    def followups(self, question, answer):
        data = self.gateway.chat_json(prompts.FOLLOWUP_SYSTEM, _json({'question': question, 'answer': answer[:400]}), model='fast', temperature=.7)
        values = _short_questions(data.get('followups'), 3, 16, [question]) if isinstance(data, dict) and set(data) == {'followups'} else []
        return {'followups': values, 'degraded': not bool(values)}

    def transcribe_image(self, image_url):
        text = self.gateway.vision(image_url)
        return {'text': text or '', 'degraded': text is None, 'reason': '' if text else '图片转写不可用，请检查图片地址及模型配置'}
