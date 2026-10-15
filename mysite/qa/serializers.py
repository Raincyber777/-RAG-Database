"""问答序列化器"""
from rest_framework import serializers

from .models import QaRecord


class QaRecordSerializer(serializers.ModelSerializer):
    note_title = serializers.CharField(source='note.title', read_only=True, default='')

    class Meta:
        model = QaRecord
        fields = ['id', 'note', 'note_title', 'question', 'answer', 'references', 'liked', 'created_at']
        read_only_fields = fields


class AskSerializer(serializers.Serializer):
    """提问参数：可指定纪要（不传则在最近一节纪要内检索）"""

    note_id = serializers.IntegerField(required=False, allow_null=True)
    question = serializers.CharField(max_length=1000, trim_whitespace=True)

    def validate_question(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('请输入问题')
        return value
