"""公告 / 协议文档序列化"""
from rest_framework import serializers

from .models import Announcement, LegalDoc


class AnnouncementSerializer(serializers.ModelSerializer):
    # 发布人可能已被删除（SET_NULL），此时展示为空串；用户端弹窗不展示发布人
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='')

    class Meta:
        model = Announcement
        fields = ['id', 'title', 'content', 'status', 'creator_name', 'created_at', 'withdrawn_at']


class LegalDocSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.CharField(source='updated_by.username', read_only=True, default='')

    class Meta:
        model = LegalDoc
        fields = ['key', 'content', 'updated_by_name', 'updated_at']
