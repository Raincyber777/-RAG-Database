"""
问题反馈接口：
- 用户端：提交反馈（可附多张截图）/ 我的反馈列表
- 管理端：全部反馈列表（?status=）+ 标记已处理
"""
from rest_framework import status
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mysite.utils import IsAdminRole, validate_image

from .models import Feedback
from .serializers import FeedbackCreateSerializer, FeedbackSerializer


class FeedbackListCreateView(ListCreateAPIView):
    """GET /api/feedback/ —— 我提交的反馈；POST —— 提交新反馈（multipart，截图 images）"""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Feedback.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        return FeedbackCreateSerializer if self.request.method == 'POST' else FeedbackSerializer

    def perform_create(self, serializer):
        feedback = serializer.save(user=self.request.user)

        # 截图：字段名 images，可多张；校验格式与大小后保存到 media/feedback/
        saved = []
        for file in self.request.FILES.getlist('images'):
            validate_image(file)
            saved.append(feedback.images.field.storage.save(f'feedback/{file.name}', file))
        if saved:
            feedback.images = saved
            feedback.save(update_fields=['images'])

        # 联系方式为空时默认使用注册邮箱
        if not feedback.contact:
            feedback.contact = feedback.user.email
            feedback.save(update_fields=['contact'])


class AdminFeedbackListView(ListAPIView):
    """GET /api/admin/feedbacks/ —— 全部反馈（?status=pending|resolved）"""

    queryset = Feedback.objects.select_related('user')
    serializer_class = FeedbackSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = super().get_queryset()
        stat = self.request.query_params.get('status')
        if stat in (Feedback.Status.PENDING, Feedback.Status.RESOLVED):
            qs = qs.filter(status=stat)
        return qs


class AdminFeedbackResolveView(APIView):
    """POST /api/admin/feedbacks/{id}/resolve/ —— 标记已处理"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk):
        feedback = Feedback.objects.filter(pk=pk).first()
        if feedback is None:
            return Response({'detail': '反馈不存在'}, status=status.HTTP_404_NOT_FOUND)
        feedback.status = Feedback.Status.RESOLVED
        feedback.save(update_fields=['status'])
        return Response(FeedbackSerializer(feedback).data)
