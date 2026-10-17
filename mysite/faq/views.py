"""
FAQ / 使用技巧接口：
- 公开：GET /api/faqs/?scope=user|admin|home、GET /api/tips/?scope=user|admin（展示页，分页）
- 管理端：新增 / 编辑 / 删除（工作台维护）
  分权：超级管理员可维护全部内容；管理员可维护用户端与首页内容（管理端内容仅超管，403 提示）
"""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mysite.utils import IsAdminRole, StandardPagination

from .models import Faq, Tip
from .serializers import FaqSerializer, TipSerializer

# FAQ 展示位：用户端使用说明 / 管理端使用说明 / 用户端首页
FAQ_SCOPES = (Faq.Scope.USER, Faq.Scope.ADMIN, Faq.Scope.HOME)
TIP_SCOPES = (Tip.Scope.USER, Tip.Scope.ADMIN)


def _check_scope_writable(request, scope: str) -> None:
    """管理员可维护用户端与首页内容，管理端内容仅超级管理员可维护"""
    if scope == Faq.Scope.ADMIN and request.user.role != 'super':
        raise PermissionDenied('仅超级管理员可编辑管理端内容')


class FaqListView(ListAPIView):
    """GET /api/faqs/?scope=user|admin|home —— 按展示位返回（默认用户端，分页）"""

    serializer_class = FaqSerializer
    permission_classes = [AllowAny]
    pagination_class = StandardPagination

    def get_queryset(self):
        scope = self.request.query_params.get('scope')
        if scope not in FAQ_SCOPES:
            scope = Faq.Scope.USER
        return Faq.objects.filter(scope=scope)


class AdminFaqListCreateView(ListCreateAPIView):
    """GET /api/admin/faqs/?scope= —— 全量列表（分页）；POST —— 新增（管理员限用户端 / 首页）"""

    queryset = Faq.objects.all()
    serializer_class = FaqSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = super().get_queryset()
        scope = self.request.query_params.get('scope')
        if scope in FAQ_SCOPES:
            qs = qs.filter(scope=scope)
        return qs

    def perform_create(self, serializer):
        _check_scope_writable(self.request, serializer.validated_data.get('scope', Faq.Scope.USER))
        serializer.save()


class AdminFaqDetailView(APIView):
    """PUT /api/admin/faqs/{id}/ —— 编辑；DELETE —— 删除（管理员仅限用户端）"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def _get(self, pk) -> Faq:
        return get_object_or_404(Faq, pk=pk)

    def put(self, request, pk):
        faq = self._get(pk)
        ser = FaqSerializer(faq, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        _check_scope_writable(request, ser.validated_data.get('scope', faq.scope))
        ser.save()
        return Response(ser.data)

    def delete(self, request, pk):
        faq = self._get(pk)
        _check_scope_writable(request, faq.scope)
        faq.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TipListView(ListAPIView):
    """GET /api/tips/?scope=user|admin —— 按端返回（默认用户端，分页）"""

    serializer_class = TipSerializer
    permission_classes = [AllowAny]
    pagination_class = StandardPagination

    def get_queryset(self):
        scope = self.request.query_params.get('scope')
        if scope not in (Tip.Scope.USER, Tip.Scope.ADMIN):
            scope = Tip.Scope.USER
        return Tip.objects.filter(scope=scope)


class AdminTipListCreateView(ListCreateAPIView):
    """GET /api/admin/tips/?scope= —— 全量列表（分页）；POST —— 新增（管理员仅限用户端）"""

    queryset = Tip.objects.all()
    serializer_class = TipSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = super().get_queryset()
        scope = self.request.query_params.get('scope')
        if scope in (Tip.Scope.USER, Tip.Scope.ADMIN):
            qs = qs.filter(scope=scope)
        return qs

    def perform_create(self, serializer):
        _check_scope_writable(self.request, serializer.validated_data.get('scope', Tip.Scope.USER))
        serializer.save()


class AdminTipDetailView(APIView):
    """PUT /api/admin/tips/{id}/ —— 编辑；DELETE —— 删除（管理员仅限用户端）"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def _get(self, pk) -> Tip:
        return get_object_or_404(Tip, pk=pk)

    def put(self, request, pk):
        tip = self._get(pk)
        ser = TipSerializer(tip, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        _check_scope_writable(request, ser.validated_data.get('scope', tip.scope))
        ser.save()
        return Response(ser.data)

    def delete(self, request, pk):
        tip = self._get(pk)
        _check_scope_writable(request, tip.scope)
        tip.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
