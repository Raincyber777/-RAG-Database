"""
FAQ 接口：
- 公开：GET /api/faqs/?scope=user|admin（使用说明页展示）
- 管理端：新增 / 编辑 / 删除（工作台「FAQ 常见问题管理」）
"""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mysite.utils import IsAdminRole

from .models import Faq
from .serializers import FaqSerializer


class FaqListView(ListAPIView):
    """GET /api/faqs/?scope=user|admin —— 按端返回（默认用户端）"""

    serializer_class = FaqSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        scope = self.request.query_params.get('scope')
        if scope not in (Faq.Scope.USER, Faq.Scope.ADMIN):
            scope = Faq.Scope.USER
        return Faq.objects.filter(scope=scope)


class AdminFaqListCreateView(ListCreateAPIView):
    """GET /api/admin/faqs/?scope= —— 全量列表；POST —— 新增"""

    queryset = Faq.objects.all()
    serializer_class = FaqSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        scope = self.request.query_params.get('scope')
        if scope in (Faq.Scope.USER, Faq.Scope.ADMIN):
            qs = qs.filter(scope=scope)
        return qs


class AdminFaqDetailView(APIView):
    """PUT /api/admin/faqs/{id}/ —— 编辑；DELETE —— 删除"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def _get(self, pk) -> Faq:
        return get_object_or_404(Faq, pk=pk)

    def put(self, request, pk):
        faq = self._get(pk)
        ser = FaqSerializer(faq, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    def delete(self, request, pk):
        self._get(pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
