"""安全模块序列化器"""
from rest_framework import serializers

from .models import HealthProfile, SensitiveAuditLog


class HealthProfileSerializer(serializers.ModelSerializer):
    """本人健康档案：字段级权限由视图保证（只暴露请求者自己的档案）。
    删除条目 = PATCH 传入对应分组的完整列表（去掉想删的条目）。"""

    class Meta:
        model = HealthProfile
        fields = ['allergies', 'medications', 'conditions', 'diet_taboos', 'updated_at']
        read_only_fields = ['updated_at']


class SensitiveAuditLogSerializer(serializers.ModelSerializer):
    """审计日志（管理端只读）：question/detail 已在写入侧脱敏。"""
    user_name = serializers.CharField(source='user.username', read_only=True, default='（已注销）')

    class Meta:
        model = SensitiveAuditLog
        fields = ['id', 'user', 'user_name', 'kind', 'question', 'action', 'detail', 'created_at']
        read_only_fields = fields
