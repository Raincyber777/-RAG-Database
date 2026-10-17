"""题库序列化器"""
from rest_framework import serializers

from .models import QuizItem, WrongRecord


class QuizItemSerializer(serializers.ModelSerializer):
    uploader_name = serializers.CharField(source='uploader.username', read_only=True)
    # 答案与解析：用户端练习页即时批改、管理端题库详情都直接使用
    answer = serializers.CharField(read_only=True)
    analysis = serializers.CharField(read_only=True, default='')

    class Meta:
        model = QuizItem
        fields = [
            'id', 'note', 'course', 'chapter', 'type', 'stem', 'options',
            'answer', 'analysis', 'uploader', 'uploader_name', 'created_at',
        ]
        read_only_fields = fields


class QuizDetailSerializer(serializers.ModelSerializer):
    """提交批改后的返回：含答案与解析"""

    class Meta:
        model = QuizItem
        fields = ['id', 'course', 'chapter', 'type', 'stem', 'options', 'answer', 'analysis']


class SubmitSerializer(serializers.Serializer):
    """作答提交：answer 为字符串（选择题为 'ABD' 形式）"""

    answer = serializers.CharField(trim_whitespace=True, allow_blank=False, max_length=2000)


class WrongRecordSerializer(serializers.ModelSerializer):
    quiz = QuizDetailSerializer(read_only=True)

    class Meta:
        model = WrongRecord
        fields = ['id', 'quiz', 'my_answer', 'created_at']
