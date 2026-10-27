"""安全模块接口：
- GET/PATCH /api/user/profile/ —— 本人健康档案（字段级权限：仅本人可读写，无管理员明文接口）
敏感内容审计只由问答 / 反馈 / 上传链路自动写入（mysite/moderation.py），不再提供人工巡检页面。
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import HealthProfile
from .serializers import HealthProfileSerializer


class HealthProfileView(APIView):
    """GET /api/user/profile/ —— 本人健康档案（无则返回空结构，不自动建档）；
    PATCH /api/user/profile/ —— 覆盖式更新分组（用户可在设置里删除某条禁忌）。
    新条目主要由问答链路的 extract_health 自动写入，这里不做词条级增补。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = HealthProfile.objects.filter(user=request.user).first()
        if profile is None:
            return Response({'allergies': [], 'medications': [], 'conditions': [], 'diet_taboos': [],
                             'updated_at': None})
        return Response(HealthProfileSerializer(profile).data)

    def patch(self, request):
        profile, _ = HealthProfile.objects.get_or_create(user=request.user)
        ser = HealthProfileSerializer(profile, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(HealthProfileSerializer(profile).data)

