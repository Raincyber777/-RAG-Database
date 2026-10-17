"""
问题反馈接口：
- 用户端：提交反馈（可附多张截图）/ 我的反馈列表
- 管理端：全部反馈列表（?status=）+ 标记已处理
"""
from django.core.files.storage import default_storage
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

    serializer_class = FeedbackSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Feedback.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        # 截图先校验再落库（字段名 images，可多张），避免校验失败留下半成品反馈
        files = request.FILES.getlist('images')
        for file in files:
            validate_image(file)

        serializer = FeedbackCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 截图存到 media/feedback/，JSONField 里只存 media 相对路径
        images = [default_storage.save(f'feedback/{f.name}', f) for f in files]

        # 联系方式为空时默认使用注册邮箱
        contact = serializer.validated_data.get('contact') or request.user.email
        feedback = serializer.save(user=request.user, images=images, contact=contact)

        # 返回完整反馈对象（与文档一致，含 id / status / user_name）
        return Response(
            FeedbackSerializer(feedback, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


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
