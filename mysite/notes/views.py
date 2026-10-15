"""
课堂纪要接口：
- 用户端：我的纪要列表 / 详情 / 上传创建 / 删除 / 重新处理
- 管理端：全部纪要巡检列表
生成流水线说明：generate_stub 目前以占位内容模拟 Agent 产物（状态机 processing → done），
接入真实流水线后将其替换为异步任务（Celery / 后台线程），接口结构不变。
"""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mysite.utils import IsAdminRole

from .models import Note
from .serializers import NoteCreateSerializer, NoteSerializer


def generate_stub(note: Note) -> Note:
    """
    TODO(真实流水线)：抽取音轨 → ffmpeg 抽帧去重 → 图片理解 → 语音转写 →
    LLM 融合生成各板块 → 切分向量化入库。当前写入占位内容并置为已完成。
    """
    note.theme = f'围绕「{note.title}」的课堂主题梳理（占位内容，待接入 Agent 生成）'
    note.points = [{'text': '核心知识点占位：接入 Agent 流水线后自动生成', 'ref': ''}]
    note.definitions = [{'term': '定义占位', 'text': '接入流水线后自动生成', 'ref': ''}]
    note.emphasized = ['重点问题占位：接入流水线后自动生成']
    note.status = Note.Status.DONE
    note.save()
    return note


class NoteListCreateView(ListCreateAPIView):
    """GET /api/notes/ —— 我的纪要（?course=&search=）；POST —— 上传创建"""

    serializer_class = NoteSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Note.objects.filter(uploader=self.request.user)
        course = self.request.query_params.get('course')
        if course:
            qs = qs.filter(course=course)
        keyword = (self.request.query_params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(title__icontains=keyword)
        return qs

    def perform_create(self, serializer):
        note = serializer.save(uploader=self.request.user)
        generate_stub(note)  # 演示：创建后立即完成占位生成

    def create(self, request, *args, **kwargs):
        serializer = NoteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(NoteSerializer(serializer.instance).data, status=status.HTTP_201_CREATED)


class NoteDetailView(APIView):
    """GET /api/notes/{id}/ —— 详情；DELETE —— 删除（本人或管理员）"""

    permission_classes = [IsAuthenticated]

    def _get(self, request, pk) -> Note:
        note = get_object_or_404(Note, pk=pk)
        if note.uploader_id != request.user.pk and not request.user.is_admin_role:
            self.permission_denied(request, message='无权访问该纪要')
        return note

    def get(self, request, pk):
        return Response(NoteSerializer(self._get(request, pk)).data)

    def delete(self, request, pk):
        note = self._get(request, pk)
        if note.source:
            note.source.delete(save=False)
        note.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NoteReprocessView(APIView):
    """POST /api/notes/{id}/reprocess/ —— 重新处理（演示环境直接完成占位生成）"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        note = get_object_or_404(Note, pk=pk)
        if note.uploader_id != request.user.pk and not request.user.is_admin_role:
            return Response({'detail': '无权操作该纪要'}, status=status.HTTP_403_FORBIDDEN)
        note.status = Note.Status.PROCESSING
        note.save(update_fields=['status'])
        generate_stub(note)
        return Response(NoteSerializer(note).data)


class AdminNoteListView(ListAPIView):
    """GET /api/admin/notes/ —— 全部纪要巡检（?course=&search=）"""

    queryset = Note.objects.select_related('uploader')
    serializer_class = NoteSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = super().get_queryset()
        course = self.request.query_params.get('course')
        if course:
            qs = qs.filter(course=course)
        keyword = (self.request.query_params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(title__icontains=keyword)
        return qs
