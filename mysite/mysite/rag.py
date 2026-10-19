"""
RAG 检索问答管线：纪要分块 → 向量化入库 → 余弦召回 → 检索优先级分档 → LLM 生成带引用答案 + 追问
- 分块：知识点 / 定义公式 / 重点问题 / 转写句子（沿用 demo 版分块口径，带来源引用）
- 向量：text-embedding-v4（1024 维，见 mysite/llm.embed）；余弦相似度 numpy 实现
- 检索优先级（知识库优先，外部知识补充）：
  Top1 相似度 ≥ KB_STRONG      → 纯知识库作答
  KB_WEAK ~ KB_STRONG          → 知识库为主，外部搜索补充（标注「补充自外部知识」，冲突以纪要为准）
  < KB_WEAK / 无片段            → 外部检索主导；两边都没有可靠依据时明确「暂时无法确认」
  外部知识经 llm.search_findings 清洗 / 去重 / 可信度判断后进入融合，来源白名单防编造
- 检索规模说明：每篇纪要几十~几百片段，Python 余弦毫秒级完成；后续数据量上来
  平迁 pgvector 时只需把 retrieve() 换成 VectorField + CosineDistance 查询
- 降级约定：answer_note() 基础设施失败（key 未配置 / 向量化失败）返回 None，
  调用方（qa.views）回退 demo 检索，保证演示闭环不依赖外网
"""
import re

import numpy as np

from mysite import llm
from notes.models import Note, NoteChunk

TOP_K = 4          # 召回片段数
MIN_SCORE = 0.05   # 相似度过低视为未召回（交给 LLM 说明依据不足，而非硬答）
DOC_CHUNK_CHARS = 280   # 无时间戳文本（讲义）按该长度聚合分块，避免长文产生过多碎片段
KB_STRONG = 0.42   # Top1 相似度 ≥ 该值：知识库足以回答，纯知识库作答
KB_WEAK = 0.22     # KB_WEAK ~ KB_STRONG：知识库部分覆盖，融合外部知识补充


def chunk_note(note: Note) -> list[dict]:
    """把纪要拆成可检索的片段：知识点 / 定义 / 重点问题 / 转写句子
    转写行带 [mm:ss] 时间戳时，ref 提取为「录音 mm:ss」，让引用可定位到录音时间点；
    讲义类（无时间戳）按段落聚合到约 DOC_CHUNK_CHARS 字符一块，兼顾检索粒度与片段总量
    """
    chunks: list[dict] = []
    for p in note.points or []:
        chunks.append({'text': p.get('text', ''), 'ref': p.get('ref') or '纪要知识点'})
    for d in note.definitions or []:
        chunks.append({'text': f"{d.get('term', '')}：{d.get('text', '')}", 'ref': d.get('ref') or '纪要定义'})
    for e in note.emphasized or []:
        chunks.append({'text': e, 'ref': '纪要重点问题'})
    buf: list[str] = []
    buf_len = 0

    def flush():
        nonlocal buf, buf_len
        if buf:
            chunks.append({'text': '。'.join(buf) + ('。' if not buf[-1].endswith('。') else ''), 'ref': '转写原文'})
            buf, buf_len = [], 0

    for sentence in (note.transcript or '').replace('。', '\n').split('\n'):
        text = sentence.strip()
        if not text:
            continue
        m = re.match(r'^\[(\d{1,2}:\d{2})\]\s*(.*)$', text)
        if m:
            flush()   # 时间戳句子独立成块，先收尾聚合缓冲
            chunks.append({'text': m.group(2) or text, 'ref': f'录音 {m.group(1)}'})
        else:
            buf.append(text)
            buf_len += len(text)
            if buf_len >= DOC_CHUNK_CHARS:
                flush()
    flush()
    return [c for c in chunks if c['text']]


def index_note(note: Note) -> bool:
    """重建一篇纪要的检索片段并向量化入库（全量替换）；key 未配置 / 向量化失败返回 False"""
    chunks = chunk_note(note)
    if not chunks:
        return False
    vecs = llm.embed([c['text'] for c in chunks])
    if vecs is None:
        return False
    NoteChunk.objects.filter(note=note).delete()
    NoteChunk.objects.bulk_create([
        NoteChunk(note=note, seq=i, text=c['text'], ref=c['ref'], embedding=v)
        for i, (c, v) in enumerate(zip(chunks, vecs))
    ])
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def retrieve_scored(note: Note, question: str, k: int = TOP_K) -> list[tuple[NoteChunk, float]] | None:
    """向量召回 Top-K 片段并携带相似度分（降序）；片段缺失时现算入库（懒加载），失败返回 None。
    分数用于检索优先级分档：决定走「纯知识库」还是「知识库 + 外部补充」。"""
    chunks = list(NoteChunk.objects.filter(note=note))
    if not chunks and not index_note(note):
        return None
    if not chunks:
        chunks = list(NoteChunk.objects.filter(note=note))
    if not chunks:
        return None
    qvec = llm.embed([question])
    if qvec is None:
        return None
    scored = [(c, _cosine(qvec[0], c.embedding)) for c in chunks if c.embedding]
    if not scored:
        return None
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [(c, s) for c, s in scored[:k] if s > MIN_SCORE]
    return top or None


def retrieve(note: Note, question: str, k: int = TOP_K) -> list[NoteChunk] | None:
    """向量召回 Top-K 片段（不带分数的便捷封装）"""
    top = retrieve_scored(note, question, k)
    return [c for c, _ in top] if top else None


def _align_refs(note: Note, top: list[NoteChunk], answer: str, fallback: bool = True) -> list[dict]:
    """引用对齐：答案里标注了 [n] 的片段优先；fallback=True 时未标注兜底展示全部召回片段
    （纯知识库档用兜底；融合档 fallback=False——模型没用知识库就不出知识库来源，
    避免「回答全靠外部、来源里却挂着知识库」的误导）；
    ref 中带 mm:ss 时间戳（如「录音 08:15」）时提取到 time 供前端挂录音定位；
    kind='kb' 标记知识库来源（与外部来源 kind='external' 区分展示）"""
    cited = sorted({int(m) for m in re.findall(r'\[\s*(\d{1,2})\s*\]', answer) if 1 <= int(m) <= len(top)})
    refs: list[dict] = []
    for n in (cited or (list(range(1, len(top) + 1)) if fallback else [])):
        c = top[n - 1]
        m = re.search(r'\d{1,2}:\d{2}', c.ref)
        refs.append({'label': note.title, 'time': m.group(0) if m else '',
                     'detail': c.ref, 'kind': 'kb'})
    return refs


# ---------- 外部知识：融合上下文 / 来源标注 ----------

def _kb_context(top: list[NoteChunk]) -> str:
    """知识库召回片段 → 提示词上下文（带 [编号] 与片段来源 ref）"""
    return '\n'.join(f'[{i + 1}] ({c.ref}) {c.text}' for i, c in enumerate(top))


def _pick_findings(question: str) -> list[dict]:
    """外部知识检索 + 解析（清洗 / 去重 / 可信度 / 摘要）；失败返回空列表"""
    return llm.search_findings(question) or []


def _external_context(findings: list[dict]) -> str:
    """外部要点 → 提示词上下文（带编号与可信度标注）"""
    lines = []
    for i, f in enumerate(findings):
        conf = llm.CONF_LABEL.get(f.get('confidence', 'mid'), '中')
        lines.append(f"[E{i + 1}] （{f['source_title']}｜可信度{conf}）{f['point']}")
    return '\n'.join(lines)


def _external_refs(findings: list[dict]) -> list[dict]:
    """外部要点 → 引用来源条目（kind='external'，带 url 供前端打开原文）"""
    return [
        {'label': f['source_title'] or f['source_url'], 'time': '',
         'detail': f['point'], 'kind': 'external', 'url': f['source_url']}
        for f in findings
    ]


def _source_line(refs: list[dict]) -> str:
    """回答末尾的来源说明（代码确定拼装而非模型复述，保证标题 / 链接真实）"""
    kb = '、'.join(dict.fromkeys(f'《{r["label"]}》{r["detail"]}' for r in refs if r.get('kind') == 'kb'))
    ext = '；'.join(dict.fromkeys(f'{r["label"]}（{r["url"]}）' for r in refs if r.get('kind') == 'external'))
    parts = []
    if kb:
        parts.append(f'知识库来源：{kb}')
    if ext:
        parts.append(f'外部来源：{ext}')
    return '\n'.join(parts)


def _no_basis_answer(note: Note) -> str:
    """知识库与外部检索都没有可靠依据时的固定回答（明确「暂时无法确认」，不编造）"""
    return (f'暂时无法确认：当前课堂纪要《{note.title}》中没有找到与该问题相关的内容，'
            '外部检索也未获得可靠依据。建议先在工作台上传相关课件或讲义，再重新提问。')


_EXTERNAL_PREFIX = '该问题在当前课堂纪要中未找到直接依据，以下为外部知识补充：\n\n'


def _mark_external(answer: str) -> str:
    """外部标注兜底：模型漏写「补充自外部知识」时，把答案里的 [E编号] 引用统一补上标注
    （顺带把模型可能输出的松散写法 [ E1 ] 规范成 [E1]）"""
    if '[E' in answer.replace(' ', '') and '补充自外部知识' not in answer:
        answer = re.sub(r'\[\s*E\s*(\d{1,2})\s*\]', r'[E\1]（补充自外部知识）', answer)
    return answer


# 三档提示词规则：知识库优先；用了外部知识要标注；冲突以纪要为准；没依据就承认。
# 输出格式按调用方拼接：一次性问答 → JSON（answer+followups）；流式 → 纯正文
# （流式的追问由 qa.views._stream_followups 依据回答独立生成，不在正文里输出）。
_KB_RULES = (
    '你是「智学纪要」的课堂助教。严格依据给定的课堂纪要片段回答学生问题：'
    '片段里有答案时用中文简洁分点作答，并在对应内容末尾用 [编号] 标注引用的片段；'
    '片段不足以回答时明确说明依据不足，再基于片段给出相近的学习建议，不要编造。'
)
_FUSION_RULES = (
    '你是「智学纪要」的课堂助教。学生提问后已同时检索课堂纪要片段与外部网络资料：'
    '1）纪要片段能回答的部分，优先依据纪要作答，并在对应内容末尾用 [编号] 标注引用的片段；'
    '2）纪要未覆盖、需要补充的内容，可引用外部资料要点（用 [E编号] 标注）；'
    '凡依据外部资料作答的句子，必须在句末注明「补充自外部知识」，一处都不能漏；'
    '3）外部资料与纪要内容冲突时，以纪要为准；'
    '4）两边都没有可靠依据的部分，明确写「暂时无法确认」，不要编造。'
)
_EXTERNAL_RULES = (
    '你是「智学纪要」的课堂助教。学生的问题在课堂纪要片段中没有找到相关内容，'
    '以下为经清洗审核的外部网络资料要点：'
    '1）直接依据外部资料要点作答（不要自己写开头说明，开头由系统统一添加）；'
    '2）每条引用用 [E编号] 标注；凡依据外部资料作答的句子，'
    '必须在句末注明「补充自外部知识」，一处都不能漏；'
    '3）外部资料仍不足以回答的部分，明确写「暂时无法确认」，不要编造。'
)
_JSON_OUT = ('最后给出 3 条学生最可能接着问的追问（每条不超过 16 字）。'
             '只输出 JSON：{"answer": "...", "followups": ["...", "...", "..."]}')
_TEXT_OUT = '直接输出回答正文，不要输出 JSON。'

# 通用模式（提问目标不存在：账号还没有任何已完成纪要）：纯外部联网检索回答，
# 课堂之外的问题也能答，来源同样经清洗审核与白名单校验
_GENERAL_RULES = (
    '你是「智学纪要」的课堂助教。学生的这个问题超出了课堂纪要的范围，'
    '以下为经清洗审核的外部网络资料要点：'
    '1）依据外部资料要点用中文分点作答（不要自己写开头说明）；'
    '2）每条引用用 [E编号] 标注；凡依据外部资料作答的句子，'
    '必须在句末注明「补充自外部知识」，一处都不能漏；'
    '3）外部资料仍不足以回答的部分，明确写「暂时无法确认」，不要编造。'
)


def no_general_answer() -> str:
    """通用模式下外部检索不可用 / 无可靠依据时的固定回答（明确说明，不编造）"""
    return ('暂时无法确认：外部检索未获得可靠依据，暂时回答不了这个问题。'
            '你可以换个问法再试，或先在「课堂纪要」上传课堂材料后针对课内内容提问。')


def _general_user(question: str, findings: list[dict]) -> str:
    """通用模式 user 消息：只有外部要点与问题（无纪要上下文）"""
    return (f'外部资料要点（已清洗去重，来源真实可引用）：\n{_external_context(findings)}'
            f'\n\n学生问题：{question}')


def answer_general(question: str) -> tuple[str, list[dict], list[str]] | None:
    """通用问答（无纪要目标）：联网检索 → 清洗 → flash 生成外部知识回答（JSON：answer + followups）。
    返回 (answer, refs, followups)；外部检索不可用 / 无可靠依据返回 None，由调用方用固定话术兜底。"""
    try:
        findings = _pick_findings(question)
        if not findings:
            return None
        data = llm.chat_json(
            [{'role': 'system', 'content': _system(_GENERAL_RULES, stream=False)},
             {'role': 'user', 'content': _general_user(question, findings)}],
            model=llm.MODEL_LITE,  # 通用回答由审核后的要点重组而来，flash 提速明显；课内问答仍走 qwen-plus
            temperature=0.3,
        )
        if not isinstance(data, dict):
            return None
        answer = str(data.get('answer') or '').strip()
        if not answer:
            return None
        answer = _mark_external(answer)
        raw_followups = data.get('followups')
        followups = ([str(x).strip() for x in raw_followups if isinstance(x, str) and x.strip()][:3]
                     if isinstance(raw_followups, list) else [])
        refs = _external_refs(findings)
        answer += '\n\n—— 来源说明 ——\n' + _source_line(refs)
        return answer, refs, followups
    except Exception:
        return None


def answer_stream_general(question: str):
    """通用问答流式版（无纪要目标）：外部检索 → 流式生成，事件与 answer_stream 一致；
    外部检索不可用时 yield 固定话术后正常结束（不降级演示检索——通用模式没有纪要可检）。"""
    try:
        findings = _pick_findings(question)
        if not findings:
            text = no_general_answer()
            yield {'type': 'delta', 'text': text}
            yield {'type': 'end', 'refs': []}
            return
        stream = llm.chat_stream(
            [{'role': 'system', 'content': _system(_GENERAL_RULES, stream=True)},
             {'role': 'user', 'content': _general_user(question, findings)}],
            model=llm.MODEL_LITE,  # 通用回答由审核后的要点重组而来，flash 提速明显；课内问答仍走 qwen-plus
            temperature=0.3,
        )
        if stream is None:
            yield {'type': 'error'}
            return
        buf = ''
        for delta in stream:
            buf += delta
            yield {'type': 'delta', 'text': delta}
        if not buf.strip():
            yield {'type': 'error'}
            return
        if '[E' in buf and '补充自外部知识' not in buf:
            note_line = '\n\n（以上标注 [E编号] 的内容均为补充自外部知识）'
            buf += note_line
            yield {'type': 'delta', 'text': note_line}
        refs = _external_refs(findings)
        tail = '\n\n—— 来源说明 ——\n' + _source_line(refs)
        buf += tail
        yield {'type': 'delta', 'text': tail}
        yield {'type': 'end', 'refs': refs}
    except Exception:
        yield {'type': 'error'}


def _system(rules: str, stream: bool) -> str:
    return rules + (_TEXT_OUT if stream else _JSON_OUT)


def answer_note(note: Note, question: str) -> tuple[str, list[dict], list[str]] | None:
    """
    完整 RAG 问答（知识库优先 + 外部知识补充）：
    向量召回分档 → qwen-plus 生成（JSON：answer + followups）→ 引用对齐 + 来源说明。
    分档：Top1 相似度 ≥ KB_STRONG 纯知识库；KB_WEAK ~ KB_STRONG 知识库为主、外部补充；
    未命中 / 无片段时外部检索主导；知识库与外部都没有可靠依据 → 明确「暂时无法确认」。
    返回 (answer, references, followups)；基础设施失败（key 未配置 / 向量化失败）返回 None
    由调用方降级演示检索。
    """
    try:
        scored = retrieve_scored(note, question)
        if not scored and not llm.available():
            return None
        best = scored[0][1] if scored else 0.0
        top = [c for c, _ in (scored or [])]
        findings = _pick_findings(question) if best < KB_STRONG else []

        if not scored or best < KB_WEAK:   # 知识库未命中（无片段或相似度过低）
            if not findings:
                answer = _no_basis_answer(note)
                return answer, [], []
            mode, rules = 'external', _EXTERNAL_RULES
        elif best >= KB_STRONG:
            mode, rules = 'kb', _KB_RULES
        elif findings:
            mode, rules = 'fusion', _FUSION_RULES
        else:   # 弱相关且外部检索失败：仍按知识库回答（模型会说明依据不足）
            mode, rules = 'kb', _KB_RULES
        system = _system(rules, stream=False)

        kb_ctx = _kb_context(top)
        ext_ctx = _external_context(findings)
        if mode == 'kb':
            user = f'课堂纪要《{note.title}》（课程：{note.course}）检索片段：\n{kb_ctx}\n\n学生问题：{question}'
        elif mode == 'fusion':
            user = (f'课堂纪要《{note.title}》（课程：{note.course}）检索片段：\n{kb_ctx}\n\n'
                    f'外部资料要点（已清洗去重，来源真实可引用）：\n{ext_ctx}\n\n学生问题：{question}')
        else:
            user = (f'课堂纪要《{note.title}》（课程：{note.course}）中未检索到与问题相关的内容。\n\n'
                    f'外部资料要点（已清洗去重，来源真实可引用）：\n{ext_ctx}\n\n学生问题：{question}')
        data = llm.chat_json(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            temperature=0.3,
        )
        if not isinstance(data, dict):
            return None
        answer = str(data.get('answer') or '').strip()
        if not answer:
            return None
        if mode == 'external':
            answer = _EXTERNAL_PREFIX + answer   # 外部主导档开头说明由代码统一添加，不依赖模型
        answer = _mark_external(answer)
        raw_followups = data.get('followups')
        followups = ([str(x).strip() for x in raw_followups if isinstance(x, str) and x.strip()][:3]
                     if isinstance(raw_followups, list) else [])
        refs = _align_refs(note, top, answer, fallback=(mode == 'kb'))
        if mode in ('fusion', 'external'):
            refs += _external_refs(findings)
        if refs:
            answer += '\n\n—— 来源说明 ——\n' + _source_line(refs)
        return answer, refs, followups
    except Exception:
        return None


def answer_stream(note: Note, question: str):
    """流式 RAG 问答生成器（知识库优先 + 外部知识补充）：向量召回分档 → 流式生成，供 SSE 逐段下发。
    事件：{'type': 'delta', 'text'} 增量 → {'type': 'end', 'refs': [...]}（引用含 kind/url，末尾补来源说明）；
    基础设施失败 / 流异常 yield {'type': 'error'}，由调用方降级演示检索。
    """
    try:
        scored = retrieve_scored(note, question)
        if not scored and not llm.available():
            yield {'type': 'error'}
            return
        best = scored[0][1] if scored else 0.0
        top = [c for c, _ in (scored or [])]
        findings = _pick_findings(question) if best < KB_STRONG else []

        if not scored or best < KB_WEAK:   # 知识库未命中（无片段或相似度过低）
            if not findings:
                text = _no_basis_answer(note)
                yield {'type': 'delta', 'text': text}
                yield {'type': 'end', 'refs': []}
                return
            mode, rules = 'external', _EXTERNAL_RULES
        elif best >= KB_STRONG:
            mode, rules = 'kb', _KB_RULES
        elif findings:
            mode, rules = 'fusion', _FUSION_RULES
        else:   # 弱相关且外部检索失败：仍按知识库回答（模型会说明依据不足）
            mode, rules = 'kb', _KB_RULES
        system = _system(rules, stream=True)

        kb_ctx = _kb_context(top)
        ext_ctx = _external_context(findings)
        if mode == 'kb':
            user = f'课堂纪要《{note.title}》（课程：{note.course}）检索片段：\n{kb_ctx}\n\n学生问题：{question}'
        elif mode == 'fusion':
            user = (f'课堂纪要《{note.title}》（课程：{note.course}）检索片段：\n{kb_ctx}\n\n'
                    f'外部资料要点（已清洗去重，来源真实可引用）：\n{ext_ctx}\n\n学生问题：{question}')
        else:
            user = (f'课堂纪要《{note.title}》（课程：{note.course}）中未检索到与问题相关的内容。\n\n'
                    f'外部资料要点（已清洗去重，来源真实可引用）：\n{ext_ctx}\n\n学生问题：{question}')
        stream = llm.chat_stream(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            temperature=0.3,
        )
        if stream is None:
            yield {'type': 'error'}
            return
        buf = ''
        if mode == 'external':
            buf += _EXTERNAL_PREFIX   # 外部主导档开头说明由代码统一添加，先于模型增量下发
            yield {'type': 'delta', 'text': _EXTERNAL_PREFIX}
        for delta in stream:
            buf += delta
            yield {'type': 'delta', 'text': delta}
        if not buf.strip():
            yield {'type': 'error'}
            return
        if '[E' in buf and '补充自外部知识' not in buf:
            note_line = '\n\n（以上标注 [E编号] 的内容均为补充自外部知识）'
            buf += note_line
            yield {'type': 'delta', 'text': note_line}
        refs = _align_refs(note, top, buf, fallback=(mode == 'kb'))
        if mode in ('fusion', 'external'):
            refs += _external_refs(findings)
        if refs:
            tail = '\n\n—— 来源说明 ——\n' + _source_line(refs)
            buf += tail
            yield {'type': 'delta', 'text': tail}
        yield {'type': 'end', 'refs': refs}
    except Exception:
        yield {'type': 'error'}
