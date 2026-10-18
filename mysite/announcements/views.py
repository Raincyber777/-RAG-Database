"""
公告 / 协议文档接口：
- 用户端：GET /api/announcements/（已发布，分页）、GET announcements/unread/（未读数+最新未读）、
  POST announcements/read/（全部已读）
- 管理端：GET/POST /api/admin/announcements/、POST {id}/withdraw/、DELETE {id}/、POST batch_delete/
- 协议文档：GET /api/legal/{key}/（公开）；GET/PUT /api/admin/legal/{key}/（仅超级管理员）
未读口径：已发布 ∧ 非本人发布 ∧ 无已读记录 —— 登录弹窗与侧边栏角标共用同一口径
"""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mysite.utils import IsAdminRole, IsSuperRole, StandardPagination

from .models import Announcement, AnnouncementRead, LegalDoc
from .serializers import AnnouncementSerializer
from .defaults import DEFAULT_LEGAL

LEGAL_KEYS = (LegalDoc.Key.TERMS, LegalDoc.Key.PRIVACY)


def _fmt_local(dt):
    """与全局 DATETIME_FORMAT 一致的本地时间串（裸 datetime 需手动转时区）"""
    return timezone.localtime(dt).strftime('%Y-%m-%d %H:%M') if dt else None


def _unread_for(user):
    """当前账号的未读公告：已发布、非本人发布、本人未读"""
    qs = Announcement.objects.filter(status=Announcement.Status.PUBLISHED)
    if user.is_authenticated:
        qs = qs.exclude(creator=user).exclude(reads__user=user)
    return qs


class AnnouncementListView(ListAPIView):
    """GET /api/announcements/ —— 已发布公告（用户端公告管理页，分页）"""
    serializer_class = AnnouncementSerializer
    permission_classes = [AllowAny]
    pagination_class = StandardPagination

    def get_queryset(self):
        return Announcement.objects.filter(status=Announcement.Status.PUBLISHED)


class AnnouncementUnreadView(APIView):
    """GET /api/announcements/unread/ —— 未读数与最新一条未读（游客恒为 0）"""
    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({'count': 0, 'latest': None})
        qs = _unread_for(request.user)
        latest = qs.first()
        return Response({
            'count': qs.count(),
            'latest': AnnouncementSerializer(latest).data if latest else None,
        })


class AnnouncementReadView(APIView):
    """POST /api/announcements/read/ —— 把全部已发布公告标记为已读（body {all:true}）"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        reads = [
            AnnouncementRead(user=request.user, announcement=a)
            for a in Announcement.objects.filter(status=Announcement.Status.PUBLISHED)
        ]
        # ignore_conflicts：重复标记时静默跳过（唯一约束兜底）
        AnnouncementRead.objects.bulk_create(reads, ignore_conflicts=True)
        return Response({'detail': '公告已全部标记为已读'})


class AdminAnnouncementListCreateView(ListCreateAPIView):
    """GET /api/admin/announcements/ —— 全量公告（含撤回，供筛选/日志）；POST —— 发布"""
    serializer_class = AnnouncementSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = StandardPagination

    def get_queryset(self):
        return Announcement.objects.all()

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)


class AdminAnnouncementWithdrawView(APIView):
    """POST /api/admin/announcements/{id}/withdraw/ —— 撤回（仅已发布可撤）"""
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk):
        a = get_object_or_404(Announcement, pk=pk)
        if a.status != Announcement.Status.PUBLISHED:
            return Response({'detail': '该公告已撤回，无需重复操作'}, status=status.HTTP_400_BAD_REQUEST)
        a.status = Announcement.Status.WITHDRAWN
        a.withdrawn_at = timezone.now()
        a.save(update_fields=['status', 'withdrawn_at'])
        return Response(AnnouncementSerializer(a).data)


class AdminAnnouncementDetailView(APIView):
    """PUT /api/admin/announcements/{id}/ —— 编辑公告（只改标题/内容，不改发布状态、不重新提醒）；
    DELETE /api/admin/announcements/{id}/ —— 删除单条（已读记录级联删除）"""
    permission_classes = [IsAuthenticated, IsAdminRole]

    def put(self, request, pk):
        a = get_object_or_404(Announcement, pk=pk)
        title = str(request.data.get('title') or '').strip()
        content = str(request.data.get('content') or '').strip()
        if not title or not content:
            return Response({'detail': '标题和内容不能为空'}, status=status.HTTP_400_BAD_REQUEST)
        if len(title) > 100:
            return Response({'detail': '公告标题不能超过 100 字'}, status=status.HTTP_400_BAD_REQUEST)
        a.title = title
        a.content = content
        a.save(update_fields=['title', 'content'])
        return Response(AnnouncementSerializer(a).data)

    def delete(self, request, pk):
        get_object_or_404(Announcement, pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminAnnouncementBatchDeleteView(APIView):
    """POST /api/admin/announcements/batch_delete/ —— 批量删除 {ids:[...]}"""
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        ids = request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            return Response({'detail': '请选择要删除的公告'}, status=status.HTTP_400_BAD_REQUEST)
        deleted, _ = Announcement.objects.filter(pk__in=ids).delete()
        return Response({'detail': f'已删除 {deleted} 条公告', 'deleted': deleted})


class LegalDocPublicView(APIView):
    """GET /api/legal/{key}/ —— 公开读取；未自定义时返回内置默认文案"""
    permission_classes = [AllowAny]

    def get(self, request, key):
        if key not in LEGAL_KEYS:
            return Response({'detail': '未知文档'}, status=status.HTTP_404_NOT_FOUND)
        doc = LegalDoc.objects.filter(key=key).first()
        content = doc.content if doc else ''
        if not content.strip():
            content = DEFAULT_LEGAL[key]
        return Response({
            'content': content,
            'updated_at': _fmt_local(doc.updated_at) if doc else None,
        })


class AdminLegalDocView(APIView):
    """GET/PUT /api/admin/legal/{key}/ —— 读取 / 保存协议文档（仅超级管理员）"""
    permission_classes = [IsAuthenticated, IsSuperRole]

    def get(self, request, key):
        if key not in LEGAL_KEYS:
            return Response({'detail': '未知文档'}, status=status.HTTP_404_NOT_FOUND)
        doc = LegalDoc.objects.filter(key=key).first()
        content = doc.content if doc else ''
        is_default = not content.strip()
        if is_default:
            content = DEFAULT_LEGAL[key]  # 未自定义：编辑器预填默认文案，可直接修改保存
        return Response({
            'content': content,
            'updated_at': _fmt_local(doc.updated_at) if doc else None,
            'updated_by': doc.updated_by.username if doc and doc.updated_by else '',
            'is_default': is_default,
        })

    def put(self, request, key):
        if key not in LEGAL_KEYS:
            return Response({'detail': '未知文档'}, status=status.HTTP_404_NOT_FOUND)
        content = str(request.data.get('content') or '').strip()
        if not content:
            return Response({'detail': '文档内容不能为空'}, status=status.HTTP_400_BAD_REQUEST)
        doc, _ = LegalDoc.objects.get_or_create(key=key)
        doc.content = content
        doc.updated_by = request.user
        doc.save(update_fields=['content', 'updated_by', 'updated_at'])
        return Response({
            'content': doc.content,
            'updated_at': _fmt_local(doc.updated_at),
            'updated_by': request.user.username,
        })
