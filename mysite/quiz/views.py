"""
题库接口：
- 用户端：按课程 / 章节 / 题型刷题、基于纪要生成练习（演示占位）、提交批改、错题本
- 管理端：全部题目巡检
"""
import re

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mysite.utils import IsAdminRole
from notes.models import Note

from .models import QuizItem, WrongRecord
from .serializers import (
    QuizDetailSerializer, QuizItemSerializer, SubmitSerializer, WrongRecordSerializer,
)


def _normalize(text: str) -> str:
    """答案归一化：去空白、去分隔符、转大写，比较时忽略顺序差异的干扰字符"""
    return re.sub(r'[\s,，、;；]+', '', (text or '')).upper()


def _is_correct(quiz: QuizItem, my_answer: str) -> bool:
    """批改：选择题比较选项字母集合；判断 / 填空 / 简答比较归一化文本"""
    mine = _normalize(my_answer)
    truth = _normalize(quiz.answer)
    if quiz.type in (QuizItem.Type.SINGLE, QuizItem.Type.MULTIPLE,
                     QuizItem.Type.INDEFINITE, QuizItem.Type.JUDGE):
        return sorted(mine) == sorted(truth)
    return mine == truth or (mine and (mine in truth or truth in mine))


class QuizListView(ListAPIView):
    """GET /api/quiz/ —— 题库列表（?course=&chapter=&type=&search=）"""

    queryset = QuizItem.objects.select_related('uploader', 'note')
    serializer_class = QuizItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        for field in ('course', 'chapter', 'type'):
            value = params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        keyword = (params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(stem__icontains=keyword)
        return qs


class QuizGenerateView(APIView):
    """
    POST /api/quiz/generate/ —— 基于纪要生成练习题
    body: {note_id, count?}（count 默认 5，上限 10）
    TODO(真实出题)：LLM 按纪要知识点生成 单选 / 多选 / 不定项 / 判断 / 填空 / 简答；
    当前占位实现：用定义与知识点生成 填空 / 简答题，保证接口闭环可演示。
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        note = Note.objects.filter(
            pk=request.data.get('note_id'), uploader=request.user,
        ).first()
        if note is None:
            return Response({'detail': '请选择自己的一节已完成纪要'}, status=status.HTTP_400_BAD_REQUEST)

        count = min(int(request.data.get('count') or 5), 10)
        created: list[QuizItem] = []

        for d in (note.definitions or [])[:count]:
            created.append(QuizItem.objects.create(
                note=note, course=note.course, chapter=note.title,
                type=QuizItem.Type.BLANK,
                stem=f'请补充：{d.get("term", "")}指的是________（来源：{note.title}）。',
                answer=d.get('text', ''),
                analysis=f'该定义出自纪要「{note.title}」的重要定义与公式板块。',
                uploader=request.user,
            ))
        for p in (note.points or [])[:count - len(created)]:
            created.append(QuizItem.objects.create(
                note=note, course=note.course, chapter=note.title,
                type=QuizItem.Type.ANSWER,
                stem=f'简述：{p.get("text", "")}（来源：{note.title}）',
                answer=p.get('text', ''),
                analysis='参考答案即该知识点的完整表述，可对照纪要原文复盘。',
                uploader=request.user,
            ))

        return Response(QuizItemSerializer(created, many=True).data, status=status.HTTP_201_CREATED)


class QuizSubmitView(APIView):
    """POST /api/quiz/{id}/submit/ —— 提交作答，即时批改（错题自动进错题本）"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        quiz = get_object_or_404(QuizItem, pk=pk)
        ser = SubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        my_answer = ser.validated_data['answer']

        correct = _is_correct(quiz, my_answer)
        if correct:
            WrongRecord.objects.filter(user=request.user, quiz=quiz).delete()
        else:
            WrongRecord.objects.update_or_create(
                user=request.user, quiz=quiz, defaults={'my_answer': my_answer},
            )
        return Response({'correct': correct, 'my_answer': my_answer, **QuizDetailSerializer(quiz).data})


class WrongListView(ListAPIView):
    """GET /api/quiz/wrong/ —— 我的错题本"""

    serializer_class = WrongRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return WrongRecord.objects.filter(user=self.request.user).select_related('quiz')


class AdminQuizListView(ListAPIView):
    """GET /api/admin/quiz/ —— 全部题目巡检（?course=&type=&search=）"""

    queryset = QuizItem.objects.select_related('uploader', 'note')
    serializer_class = QuizItemSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        for field in ('course', 'type'):
            value = params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        keyword = (params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(stem__icontains=keyword)
        return qs
