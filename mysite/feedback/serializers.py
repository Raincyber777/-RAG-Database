"""问题反馈序列化器"""
from django.core.files.storage import default_storage
from rest_framework import serializers

from .models import Feedback


class FeedbackSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.username', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = Feedback
        fields = ['id', 'title', 'content', 'contact', 'images', 'status',
                  'user', 'user_name', 'user_email', 'created_at']
        read_only_fields = ['id', 'status', 'user', 'user_name', 'user_email', 'created_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # 截图输出完整 URL（存储字段里是 media 相对路径）
        request = self.context.get('request')
        urls = [default_storage.url(p) for p in data['images']]
        data['images'] = [request.build_absolute_uri(url) for url in urls] if request else urls
        return data


class FeedbackCreateSerializer(serializers.ModelSerializer):
    """提交反馈：multipart/form-data，截图字段名为 images（可多张）"""

    class Meta:
        model = Feedback
        fields = ['title', 'content', 'contact']

    def validate_title(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('请输入标题')
        return value

    def validate_content(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('请输入详细描述')
        return value
