"""
RAG 检索问答管线：纪要分块 → 向量化入库 → 余弦召回 → LLM 生成带引用答案 + 追问
- 分块：知识点 / 定义公式 / 重点问题 / 转写句子（沿用 demo 版分块口径，带来源引用）
- 向量：text-embedding-v4（1024 维，见 mysite/llm.embed）；余弦相似度 numpy 实现
- 检索规模说明：每篇纪要几十~几百片段，Python 余弦毫秒级完成；后续数据量上来
  平迁 pgvector 时只需把 retrieve() 换成 VectorField + CosineDistance 查询
- 降级约定：answer_note() 任一环节失败返回 None，调用方（qa.views）回退 demo 检索，
  保证演示闭环不依赖外网
"""
import re

import numpy as np

from mysite import llm
from notes.models import Note, NoteChunk

TOP_K = 4          # 召回片段数
MIN_SCORE = 0.05   # 相似度过低视为未召回（交给 LLM 说明依据不足，而非硬答）


def chunk_note(note: Note) -> list[dict]:
    """把纪要拆成可检索的片段：知识点 / 定义 / 重点问题 / 转写句子
    转写行带 [mm:ss] 时间戳时，ref 提取为「录音 mm:ss」，让引用可定位到录音时间点
    """
    chunks: list[dict] = []
    for p in note.points or []:
        chunks.append({'text': p.get('text', ''), 'ref': p.get('ref') or '纪要知识点'})
    for d in note.definitions or []:
        chunks.append({'text': f"{d.get('term', '')}：{d.get('text', '')}", 'ref': d.get('ref') or '纪要定义'})
    for e in note.emphasized or []:
        chunks.append({'text': e, 'ref': '纪要重点问题'})
    for sentence in (note.transcript or '').replace('。', '\n').split('\n'):
        text = sentence.strip()
        if not text:
            continue
        m = re.match(r'^\[(\d{1,2}:\d{2})\]\s*(.*)$', text)
        if m:
            chunks.append({'text': m.group(2) or text, 'ref': f'录音 {m.group(1)}'})
        else:
            chunks.append({'text': text, 'ref': '转写原文'})
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


def retrieve(note: Note, question: str, k: int = TOP_K) -> list[NoteChunk] | None:
    """向量召回 Top-K 片段；片段缺失时现算入库（懒加载），整体失败返回 None"""
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
    top = [c for c, s in scored[:k] if s > MIN_SCORE]
    return top or None


def _align_refs(note: Note, top: list[NoteChunk], answer: str) -> list[dict]:
    """引用对齐：答案里标注了 [n] 的片段优先，未标注时兜底展示全部召回片段；
    ref 中带 mm:ss 时间戳（如「录音 08:15」）时提取到 time 供前端挂录音定位"""
    cited = sorted({int(m) for m in re.findall(r'\[(\d{1,2})\]', answer) if 1 <= int(m) <= len(top)})
    refs: list[dict] = []
    for n in (cited or list(range(1, len(top) + 1))):
        c = top[n - 1]
        m = re.search(r'\d{1,2}:\d{2}', c.ref)
        refs.append({'label': note.title, 'time': m.group(0) if m else '', 'detail': c.ref})
    return refs


def answer_note(note: Note, question: str) -> tuple[str, list[dict], list[str]] | None:
    """
    完整 RAG 问答：召回 → qwen-plus 生成（JSON：answer + followups）→ 引用对齐。
    返回 (answer, references, followups)；失败返回 None 由调用方降级。
    """
    try:
        top = retrieve(note, question)
        if not top:
            return None
        context = '\n'.join(f'[{i + 1}] ({c.ref}) {c.text}' for i, c in enumerate(top))
        system = (
            '你是「智学纪要」的课堂助教。严格依据给定的课堂纪要片段回答学生问题：'
            '片段里有答案时用中文简洁分点作答，并在对应内容末尾用 [编号] 标注引用的片段；'
            '片段不足以回答时明确说明依据不足，再基于片段给出相近的学习建议，不要编造。'
            '最后给出 3 条学生最可能接着问的追问（每条不超过 16 字）。'
            '只输出 JSON：{"answer": "...", "followups": ["...", "...", "..."]}'
        )
        user = f'课堂纪要《{note.title}》（课程：{note.course}）检索片段：\n{context}\n\n学生问题：{question}'
        data = llm.chat_json(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            temperature=0.3,
        )
        if not isinstance(data, dict):
            return None
        answer = str(data.get('answer') or '').strip()
        if not answer:
            return None
        raw_followups = data.get('followups')
        followups = ([str(x).strip() for x in raw_followups if isinstance(x, str) and x.strip()][:3]
                     if isinstance(raw_followups, list) else [])
        return answer, _align_refs(note, top, answer), followups
    except Exception:
        return None


def answer_stream(note: Note, question: str):
    """流式 RAG 问答生成器：向量召回 → qwen-plus 流式生成，供 SSE 逐段下发。
    事件：{'type': 'delta', 'text'} 增量 → {'type': 'end', 'refs': [...]}（引用已按全文对齐）；
    召回失败 / 模型不可用 / 流异常 yield {'type': 'error'}，由调用方降级演示检索。
    """
    try:
        top = retrieve(note, question)
        if not top:
            yield {'type': 'error'}
            return
        context = '\n'.join(f'[{i + 1}] ({c.ref}) {c.text}' for i, c in enumerate(top))
        system = (
            '你是「智学纪要」的课堂助教。严格依据给定的课堂纪要片段回答学生问题：'
            '片段里有答案时用中文简洁分点作答，并在对应内容末尾用 [编号] 标注引用的片段；'
            '片段不足以回答时明确说明依据不足，再基于片段给出相近的学习建议，不要编造。'
            '直接输出回答正文，不要输出 JSON。'
        )
        user = f'课堂纪要《{note.title}》（课程：{note.course}）检索片段：\n{context}\n\n学生问题：{question}'
        stream = llm.chat_stream(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
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
        yield {'type': 'end', 'refs': _align_refs(note, top, buf)}
    except Exception:
        yield {'type': 'error'}
