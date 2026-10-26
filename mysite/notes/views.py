"""
课堂纪要接口：
- 用户端：我的纪要列表 / 详情 / 上传创建 / 删除 / 重新处理
- 管理端：全部纪要巡检列表
生成流水线：上传创建 / 重新处理后启动后台线程（notes/services.start_processing），
按材料类型抽取（ASR 转写 / 视觉识别 / 文本直读）→ LLM 生成 → 向量化入库；
接口立即返回 processing 状态，前端轮询详情直至 done / failed。
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
from .services import start_processing


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
        start_processing(note)  # 后台线程执行真实流水线，接口先返回 processing

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
    """POST /api/notes/{id}/reprocess/ —— 重新处理（后台重新跑流水线）"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        note = get_object_or_404(Note, pk=pk)
        if note.uploader_id != request.user.pk and not request.user.is_admin_role:
            return Response({'detail': '无权操作该纪要'}, status=status.HTTP_403_FORBIDDEN)
        start_processing(note)
        return Response(NoteSerializer(note).data)


class CourseRenameView(APIView):
    """POST /api/notes/course-rename/ —— 课程重命名（body: {from, to}，改到已有课程即合并）"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        old = (request.data.get('from') or '').strip()
        new = (request.data.get('to') or '').strip()
        if not old or not new:
            return Response({'detail': '请提供原课程名与新课程名'}, status=status.HTTP_400_BAD_REQUEST)
        if old == new:
            return Response({'detail': '新名称与原名称相同'}, status=status.HTTP_400_BAD_REQUEST)
        updated = Note.objects.filter(uploader=request.user, course=old).update(course=new)
        return Response({'course': new, 'updated': updated})


class CourseDeleteView(APIView):
    """POST /api/notes/course-delete/ —— 删除课程及其名下全部纪要（body: {course}）"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        course = (request.data.get('course') or '').strip()
        if not course:
            return Response({'detail': '请提供课程名'}, status=status.HTTP_400_BAD_REQUEST)
        notes = Note.objects.filter(uploader=request.user, course=course)
        for n in notes:
            if n.source:
                n.source.delete(save=False)
        deleted = notes.count()
        notes.delete()  # 问答 / 练习 / 错题随外键级联删除
        return Response({'course': course, 'deleted': deleted})


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


class AdminNoteBatchDeleteView(APIView):
    """POST /api/admin/notes/batch_delete/ —— 批量删除课堂纪要（body: {ids: [...]}）
    纪要名下的片段、问答记录（含反馈流水）、自创题与错题随外键级联一并清除；
    返回实际删除条数。"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        ids = request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            return Response({'detail': '请先勾选要删除的纪要'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            id_list = [int(x) for x in ids]
        except (TypeError, ValueError):
            return Response({'detail': '记录标识有误'}, status=status.HTTP_400_BAD_REQUEST)
        qs = Note.objects.filter(pk__in=id_list)
        deleted = qs.count()
        for note in qs:  # 源文件是媒体磁盘文件，不随 CASCADE 清理，需先删
            if note.source:
                note.source.delete(save=False)
        qs.delete()
        return Response({'detail': f'已删除 {deleted} 条纪要', 'deleted': deleted})
