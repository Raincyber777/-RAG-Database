"""问答序列化器"""
from rest_framework import serializers

from .models import QaRecord


class QaRecordSerializer(serializers.ModelSerializer):
    # 通用问答（无纪要）note 为空：主键用 method 返回 note_id，标题 / 课程回退空串
    note = serializers.SerializerMethodField()
    note_title = serializers.CharField(source='note.title', read_only=True, default='')
    # 提问者与所属课程（管理端巡检页展示用；纪要被删后课程为空）
    user_name = serializers.CharField(source='user.username', read_only=True)
    course = serializers.CharField(source='note.course', read_only=True, default='')

    def get_note(self, obj):
        return obj.note_id

    class Meta:
        model = QaRecord
        fields = ['id', 'note', 'note_title', 'course', 'user_name', 'question', 'answer',
                  'references', 'followups', 'liked', 'created_at', 'session_ref']
        read_only_fields = fields


class AskSerializer(serializers.Serializer):
    """提问参数：可指定纪要（不传则在最近一节纪要内检索）；
    session_ref 为前端自由对话的会话标识（'qa'+时间戳），用于刷新后按会话拉回历史。
    """

    note_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    question = serializers.CharField(max_length=1000, trim_whitespace=True)
    session_ref = serializers.CharField(required=False, allow_blank=True, max_length=64)

    def validate_question(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('请输入问题')
        return value
