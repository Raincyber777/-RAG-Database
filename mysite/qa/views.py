"""
问答接口：
- 用户端：提问（一次性 / SSE 流式，返回答案 + 引用来源 + 追问）/ 历史记录 / 点赞
- 管理端：全部问答记录巡检
"""
import json

from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mysite.utils import IsAdminRole
from mysite import llm, rag
from notes.models import Note

from .models import QaRecord
from .serializers import AskSerializer, QaRecordSerializer
from . import agent_bridge
from .sessions import prepare_session, save_answer
from .images import prepare_images, image_events


def _score(question: str, text: str) -> int:
    """演示检索打分：问题与片段的 2-gram 重合数。TODO: 替换为向量相似度召回。"""
    q = ''.join(ch for ch in question if ch.isalnum())
    if len(q) < 2:
        return 0
    grams = {q[i:i + 2] for i in range(len(q) - 1)}
    return sum(1 for i in range(len(text) - 1) if text[i:i + 2] in grams)


def demo_answer(note: Note, question: str) -> tuple[str, list[dict]]:
    """
    演示版检索问答：按 2-gram 重合度取 Top3 片段拼装答案与引用来源。
    作为 RAG（mysite/rag.answer_note）失败时的降级路径，保证问答闭环不依赖外网。
    """
    chunks = rag.chunk_note(note)
    top = sorted(chunks, key=lambda c: _score(question, c['text']), reverse=True)[:3]
    top = [c for c in top if _score(question, c['text']) > 0] or chunks[:1]
    refs = [{'label': f'{note.title}', 'time': '', 'detail': c['ref']} for c in top]
    body = '\n'.join(f'· {c["text"]}' for c in top)
    answer = f'根据《{note.title}》的纪要内容：\n{body}\n\n以上内容可在「引用来源」中核对原文（演示检索）。'
    return answer, refs


class SuggestionsView(APIView):
    """GET /api/qa/suggestions/ —— 欢迎页个性化追问建议
    依据近期提问涉及的纪要生成；配置了 DASHSCOPE_API_KEY 时由轻量 LLM 生成，
    未配置 / 调用失败时降级为规则版（模板拼装），保证演示闭环不依赖外网。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        cached = _cached_suggestions(user)
        if cached:
            return Response({'suggestions': cached})
        recent = list(QaRecord.objects.filter(user=user).select_related('note')[:6])
        # 通用问答记录（note 为空）不参与纪要向推荐，避免 None 崩溃
        note_records = [r for r in recent if r.note_id]
        if note_records:
            items = _llm_suggestions(note_records) or _rule_suggestions(note_records)
        elif recent:
            # 只有过自由问答（账号无已完成纪要）：通用知识向推荐，走联网通用问答
            items = list(GENERAL_SUGGESTIONS)
        else:
            # 还没提过问：优先从本人最新纪要出推荐题；无纪要则全通用
            latest = list(Note.objects.filter(uploader=user, status=Note.Status.DONE).order_by('-created_at')[:2])
            items = [f'《{n.title}》的重点是什么？' for n in latest] + DEFAULT_SUGGESTIONS[:2] if latest else list(GENERAL_SUGGESTIONS)
        items = items[:4]
        # 缓存到「当天 / 未提新问题」，避免每次进页面都调模型
        latest_pk = QaRecord.objects.filter(user=user).values_list('pk', flat=True).first()
        _SUGGESTION_CACHE[user.pk] = (latest_pk or 0, timezone.localdate().isoformat(), items)
        return Response({'suggestions': items})


# 建议缓存：{user_id: (最新问答 pk, 日期, items)} —— 提了新问题或跨天自动重新生成
_SUGGESTION_CACHE: dict[int, tuple[int, str, list[str]]] = {}


def _cached_suggestions(user) -> list[str] | None:
    latest_pk = QaRecord.objects.filter(user=user).values_list('pk', flat=True).first()
    hit = _SUGGESTION_CACHE.get(user.pk)
    if hit and hit[0] == (latest_pk or 0) and hit[1] == timezone.localdate().isoformat():
        return hit[2]
    return None


DEFAULT_SUGGESTIONS = [
    '帮我总结上一节课的重点',
    '泰勒展开有哪些易错点？',
    '进程和线程的区别是什么？',
    '二叉树的四种遍历怎么背？',
]

# 无纪要账号（未上传任何课堂资料）的通用推荐：不预设课堂内容，走联网通用问答
GENERAL_SUGGESTIONS = [
    '用通俗的话解释什么是机器学习',
    '帮我制定高数期末复习计划',
    '英语四级作文有哪些万能句型？',
    '什么是毕达哥拉斯定理？',
]


def _rule_suggestions(recent: list[QaRecord]) -> list[str]:
    """规则版个性化：最近提问的纪要 → 追问定义 / 重点问题 / 复习清单，不足 4 条用兜底补齐。"""
    out: list[str] = []
    notes: list[Note] = []
    for r in recent:
        if not any(n.id == r.note_id for n in notes):
            notes.append(r.note)

    last = recent[0]
    terms = [d.get('term') for d in (last.note.definitions or []) if d.get('term')]
    if terms:
        out.append(f'再考考我：《{last.note.title}》里的「{terms[0]}」')
    else:
        out.append(f'《{last.note.title}》这节课还有哪些重点没讲到？')

    if len(notes) >= 2:
        emphasized = notes[1].emphasized or []
        q = str(emphasized[0]).strip() if emphasized else ''
        for prefix in ('注意：', '注意:', '重点：', '重点:'):  # 剥掉常见前缀，避免「《标题》：注意：…」拗口
            if q.startswith(prefix):
                q = q[len(prefix):].strip()
        out.append(f'《{notes[1].title}》：{q}' if q else f'《{notes[1].title}》有哪些易错点？')
        out.append(f'把《{notes[0].course}》最近问过的内容串成一份复习清单')

    for s in DEFAULT_SUGGESTIONS:
        if len(out) >= 4:
            break
        if s not in out:
            out.append(s)
    return out[:4]


def _llm_suggestions(recent: list[QaRecord]) -> list[str] | None:
    """LLM 个性化追问：把近期提问 + 纪要上下文交给轻量模型，输出 4 条建议；失败返回 None。"""
    lines = [f'- 问题：{r.question}（纪要《{r.note.title}》/ {r.note.course}）' for r in recent[:6]]
    prompt = (
        '你是课堂学习助手。根据学生近期的提问记录，生成 4 条他接下来最可能想问的追问建议。'
        '要求：中文、口语化、每条不超过 18 字、不要与原问题重复、紧扣下面这些课程内容。\n'
        '近期提问：\n' + '\n'.join(lines) +
        '\n只输出 JSON：{"suggestions": ["...", "...", "...", "..."]}'
    )
    data = llm.chat_json([{'role': 'user', 'content': prompt}], model=llm.MODEL_LITE, temperature=0.8)
    if not isinstance(data, dict):
        return None
    items = data.get('suggestions')
    if not isinstance(items, list):
        return None
    clean = [str(x).strip() for x in items if isinstance(x, str) and 2 <= len(x.strip()) <= 30]
    return clean[:4] if len(clean) >= 2 else None


def _resolve_ask_note(request, note_id) -> Note | None:
    """提问目标纪要：显式指定时校验归属（本人或管理员），否则取本人最新已完成纪要"""
    if note_id is not None:
        notes = Note.objects.filter(pk=note_id)
        if not request.user.is_admin_role:
            notes = notes.filter(uploader=request.user)
        note = notes.first()
        if note is None:
            raise NotFound('纪要不存在或无权访问')
        if note.status != Note.Status.DONE:
            raise ValidationError({'detail': '请等待当前纪要处理完成后再提问'})
        return note
    return Note.objects.filter(uploader=request.user, status=Note.Status.DONE).first()


class AskView(APIView):
    """POST /api/qa/ask/ —— 提问，一次性返回完整回答（body: {note_id?, question}）"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = AskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        note = _resolve_ask_note(request, data.get('note_id'))
        session = prepare_session(request.user, data, note)
        if session.note_id:
            note = _resolve_ask_note(request, session.note_id)
        question = data['question']
        images = prepare_images(data, session, request.user)
        if images:
            for event, payload in image_events(session, request.user, note, question, images):
                if event == 'done':
                    response = Response(payload, status=status.HTTP_201_CREATED)
                    response['X-QA-Engine'] = 'vision'
                    return response
                if event == 'error':
                    return Response(payload, status=status.HTTP_502_BAD_GATEWAY)


        use_agent = agent_bridge.enabled()
        if use_agent:
            result = agent_bridge.answer(request.user, note, question)
            answer, refs, followups = result['answer'], result['references'], result['followups']
        elif note is None:
            # 无纪要目标（账号还没有已完成纪要）：通用模式，联网检索回答课堂之外的问题
            result = rag.answer_general(question)
            if result is not None:
                answer, refs, followups = result
            else:
                answer, refs, followups = rag.no_general_answer(), [], []
        else:
            # 真 RAG：向量召回 + LLM 生成（含追问）；key 未配置 / 检索生成失败时降级演示检索
            result = rag.answer_note(note, question)
            if result is not None:
                answer, refs, followups = result
            else:
                answer, refs = demo_answer(note, question)
                followups = []
        record = save_answer(session,
            user=request.user, note=note, question=question,
            answer=answer, references=refs, followups=followups,
        )
        response = Response(QaRecordSerializer(record).data, status=status.HTTP_201_CREATED)
        response['X-QA-Engine'] = 'agent' if use_agent else 'legacy'
        return response


def _stream_followups(note: Note | None, question: str, answer: str) -> list[str]:
    """流式回答完成后的追问建议：轻量模型依据问题与回答节选独立生成；失败返回空（通用问答无纪要上下文）"""
    head = (f'学生刚在《{note.title}》（{note.course}）提问：「{question}」'
            if note else f'学生刚提问：「{question}」')
    prompt = (
        f'{head}，助教已作答（节选）：{answer[:400]}'
        '\n请给 3 条学生最可能接着问的追问，中文、每条不超过 16 字。'
        '只输出 JSON：{"followups": ["...", "...", "..."]}'
    )
    data = llm.chat_json([{'role': 'user', 'content': prompt}], model=llm.MODEL_LITE, temperature=0.7)
    if not isinstance(data, dict) or not isinstance(data.get('followups'), list):
        return []
    return [str(x).strip() for x in data['followups']
            if isinstance(x, str) and 2 <= len(x.strip()) <= 20][:3]


def _sse(event: str, payload: dict) -> str:
    """SSE 帧：event + data 两行，空行结尾（ensure_ascii=False 保住中文可读性）"""
    return f'event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'


class AskStreamView(APIView):
    """POST /api/qa/ask/stream/ —— SSE 流式提问（text/event-stream）
    Agent 事件：status / delta / replace → done（已入库的完整问答记录）。
    replace 覆盖临时答案，只有最终答案入库；保留通用问答及 session_ref。
    AGENT_ENABLED=0 或缺少包/模型 key 时沿用旧 RAG 路径。
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = AskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        note = _resolve_ask_note(request, data.get('note_id'))
        session = prepare_session(request.user, data, note)
        if session.note_id:
            note = _resolve_ask_note(request, session.note_id)
        question = data['question']
        images = prepare_images(data, session, request.user)
        if images:
            def vision_sse():
                from contextlib import closing
                with closing(image_events(session, request.user, note, question, images)) as events:
                    for event, payload in events:
                        yield _sse(event, payload)
            response = StreamingHttpResponse(vision_sse(), content_type='text/event-stream')
            response['Cache-Control'] = 'no-cache'
            response['X-Accel-Buffering'] = 'no'
            response['X-QA-Engine'] = 'vision'
            return response
        general = note is None   # 无纪要目标：通用模式（纯外部联网检索，可答课堂之外的问题）
        use_agent = agent_bridge.enabled()

        def sse():
            if use_agent:
                from contextlib import closing
                with closing(agent_bridge.stream(request.user, note, question)) as events:
                    for event in events:
                        if event['event'] == 'done':
                            record = save_answer(session,
                                user=request.user, note=note, question=question,
                                **event['data'],
                            )
                            yield _sse('done', QaRecordSerializer(record).data)
                            return
                        yield _sse(event['event'], event['data'])
                return
            buf, refs = '', []
            try:
                gen = rag.answer_stream_general(question) if general else rag.answer_stream(note, question)
                for ev in gen:
                    if ev['type'] == 'delta':
                        buf += ev['text']
                        yield _sse('delta', {'text': ev['text']})
                    elif ev['type'] == 'end':
                        refs = ev.get('refs') or []
                        break
                    else:  # error：已有增量则就地收尾，否则走演示降级
                        break
            except Exception:
                pass  # 流中断：以已生成内容收尾
            if not buf:
                if general:
                    buf = rag.no_general_answer()
                    yield _sse('delta', {'text': buf})
                else:
                    buf, refs = demo_answer(note, question)
                    yield _sse('delta', {'text': buf})
            record = save_answer(session,
                user=request.user, note=note, question=question,
                answer=buf, references=refs, followups=_stream_followups(note, question, buf),
                )
            yield _sse('done', QaRecordSerializer(record).data)

        resp = StreamingHttpResponse(sse(), content_type='text/event-stream')
        resp['Cache-Control'] = 'no-cache'
        resp['X-Accel-Buffering'] = 'no'
        resp['X-QA-Engine'] = 'agent' if use_agent else 'legacy'
        return resp


class QaListView(ListAPIView):
    """GET /api/qa/ —— 我的问答历史（?search=&note=，note 为纪要 id 时只取该课的追问）"""

    serializer_class = QaRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = QaRecord.objects.filter(user=self.request.user).select_related('note')
        note_id = self.request.query_params.get('note')
        if note_id:
            qs = qs.filter(note_id=note_id)
        # 按前端自由对话会话拉历史（刷新后恢复会话消息）
        session = (self.request.query_params.get('session') or '').strip()
        if session:
            qs = qs.filter(session_ref=session).order_by('created_at', 'id')
        keyword = (self.request.query_params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(question__icontains=keyword)
        return qs


class QaLikeView(APIView):
    """POST /api/qa/{id}/like/ —— 点赞 / 取消点赞"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        record = QaRecord.objects.filter(pk=pk, user=request.user).first()
        if record is None:
            return Response({'detail': '记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        record.liked = not record.liked
        record.save(update_fields=['liked'])
        return Response(QaRecordSerializer(record).data)


class AdminQaListView(ListAPIView):
    """GET /api/admin/qa/ —— 全部问答记录（?search=）"""

    queryset = QaRecord.objects.select_related('user', 'note')
    serializer_class = QaRecordSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = super().get_queryset()
        keyword = (self.request.query_params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(question__icontains=keyword)
        return qs
