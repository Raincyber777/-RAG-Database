"""安全模块序列化器"""
from rest_framework import serializers

from .models import HealthProfile


class HealthProfileSerializer(serializers.ModelSerializer):
    """本人健康档案：字段级权限由视图保证（只暴露请求者自己的档案）。
    删除条目 = PATCH 传入对应分组的完整列表（去掉想删的条目）。"""

    class Meta:
        model = HealthProfile
        fields = ['allergies', 'medications', 'conditions', 'diet_taboos', 'updated_at']
        read_only_fields = ['updated_at']
