"""
问答接口：
- 用户端：提问（返回答案 + 引用来源）/ 历史记录 / 点赞
- 管理端：全部问答记录巡检
"""
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mysite.utils import IsAdminRole
from notes.models import Note

from .models import QaRecord
from .serializers import AskSerializer, QaRecordSerializer


def _chunks_of(note: Note) -> list[dict]:
    """把纪要拆成可检索的片段：知识点 / 定义 / 重点问题 / 转写句子"""
    chunks: list[dict] = []
    for p in note.points or []:
        chunks.append({'text': p.get('text', ''), 'ref': p.get('ref') or '纪要知识点'})
    for d in note.definitions or []:
        chunks.append({'text': f"{d.get('term', '')}：{d.get('text', '')}", 'ref': d.get('ref') or '纪要定义'})
    for e in note.emphasized or []:
        chunks.append({'text': e, 'ref': '纪要重点问题'})
    for sentence in (note.transcript or '').replace('。', '\n').split('\n'):
        if sentence.strip():
            chunks.append({'text': sentence.strip(), 'ref': '转写原文'})
    return [c for c in chunks if c['text']]


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
    TODO(真实 RAG)：pgvector 相似度检索 → LLM 生成 → 引用对齐时间戳/页码。
    """
    chunks = _chunks_of(note)
    top = sorted(chunks, key=lambda c: _score(question, c['text']), reverse=True)[:3]
    top = [c for c in top if _score(question, c['text']) > 0] or chunks[:1]
    refs = [{'label': f'{note.title}', 'time': '', 'detail': c['ref']} for c in top]
    body = '\n'.join(f'· {c["text"]}' for c in top)
    answer = f'根据《{note.title}》的纪要内容：\n{body}\n\n以上内容可在「引用来源」中核对原文（演示检索）。'
    return answer, refs


class AskView(APIView):
    """POST /api/qa/ask/ —— 提问（body: {note_id?, question}）"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = AskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        note = None
        if data.get('note_id'):
            note = Note.objects.filter(pk=data['note_id']).first()
            if note and note.uploader_id != request.user.pk and not request.user.is_admin_role:
                note = None
        if note is None:
            note = Note.objects.filter(uploader=request.user, status=Note.Status.DONE).first()
        if note is None:
            return Response(
                {'detail': '请先在「课堂纪要」完成一节课的纪要生成后再提问'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        answer, refs = demo_answer(note, data['question'])
        record = QaRecord.objects.create(
            user=request.user, note=note, question=data['question'],
            answer=answer, references=refs,
        )
        return Response(QaRecordSerializer(record).data, status=status.HTTP_201_CREATED)


class QaListView(ListAPIView):
    """GET /api/qa/ —— 我的问答历史（?search=）"""

    serializer_class = QaRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = QaRecord.objects.filter(user=self.request.user).select_related('note')
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
