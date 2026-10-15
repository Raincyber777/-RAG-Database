"""FAQ 序列化器"""
from rest_framework import serializers

from .models import Faq


class FaqSerializer(serializers.ModelSerializer):
    class Meta:
        model = Faq
        fields = ['id', 'scope', 'q', 'a', 'order', 'updated_at']

    def validate_q(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('请输入问题标题')
        return value

    def validate_a(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('请输入解决描述')
        return value
