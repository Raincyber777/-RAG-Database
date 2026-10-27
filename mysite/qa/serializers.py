"""问答序列化器"""
from rest_framework import serializers
from django.core.files.storage import default_storage

from .models import KnowledgeGap, QaRecord


class QaRecordSerializer(serializers.ModelSerializer):
    attachments = serializers.SerializerMethodField()

    def get_attachments(self, obj):
        return [{'id': str(obj.pk) + '-' + str(i), 'name': item['name'], 'kind': 'image',
                 'size': item['size'], 'url': default_storage.url(item['path'])}
                for i, item in enumerate(obj.images)]

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
                  'references', 'followups', 'liked', 'created_at', 'session_ref',
                  'question_rewrite', 'retrieval', 'used_external', 'profile_updated', 'moderation', 'attachments']
        read_only_fields = fields


class AskSerializer(serializers.Serializer):
    """提问参数：可指定纪要（不传则在最近一节纪要内检索）；
    session_ref 为前端自由对话的会话标识（'qa'+时间戳），用于刷新后按会话拉回历史。
    """

    note_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    question = serializers.CharField(max_length=1000, trim_whitespace=True, required=False, allow_blank=True)
    session_ref = serializers.CharField(required=False, allow_blank=True, max_length=64)

    images = serializers.ListField(child=serializers.FileField(), required=False, max_length=8, allow_empty=False, write_only=True)
    image_record_id = serializers.IntegerField(required=False, min_value=1)

    def validate(self, data):
        from .images import normalize_image, MAX_TOTAL_BYTES
        if data.get('images') and data.get('image_record_id'):
            raise serializers.ValidationError('不能同时上传图片和引用历史图片')
        files = data.get('images', [])
        if sum(f.size for f in files) > MAX_TOTAL_BYTES:
            raise serializers.ValidationError('图片总大小不能超过 40MB')
        if files:
            data['images'] = [normalize_image(file) for file in files]
        question = data.get('question', '').strip()
        if not question and not files and not data.get('image_record_id'):
            raise serializers.ValidationError({'question': '请输入问题或上传图片'})
        data['question'] = question or '请描述并分析这些图片中的内容。'
        return data
