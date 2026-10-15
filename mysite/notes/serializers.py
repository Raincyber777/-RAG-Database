"""课堂纪要序列化器"""
from rest_framework import serializers

from .models import Note


class NoteSerializer(serializers.ModelSerializer):
    uploader_name = serializers.CharField(source='uploader.username', read_only=True)

    class Meta:
        model = Note
        fields = [
            'id', 'course', 'title', 'duration', 'status', 'uploader', 'uploader_name',
            'source', 'source_type', 'theme', 'points', 'definitions', 'emphasized',
            'transcript', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'status', 'uploader', 'theme', 'points', 'definitions',
                            'emphasized', 'transcript', 'created_at', 'updated_at']


class NoteCreateSerializer(serializers.ModelSerializer):
    """创建纪要：课程 + 主题 + 可选材料文件；材料类型按扩展名识别"""

    ALLOWED_EXT = {
        'mp3', 'wav', 'm4a', 'aac', 'flac',            # 录音
        'mp4', 'webm', 'mov', 'avi', 'mkv',            # 视频
        'ppt', 'pptx', 'pdf', 'png', 'jpg', 'jpeg',    # PPT / 板书照片
    }

    class Meta:
        model = Note
        fields = ['course', 'title', 'duration', 'source', 'source_type']

    def validate_source(self, file):
        if file is None:
            return file
        name = (file.name or '').lower()
        ext = name.rsplit('.', 1)[-1] if '.' in name else ''
        if ext not in self.ALLOWED_EXT:
            raise serializers.ValidationError('不支持的材料格式（录音 / 视频 / PPT / 图片）')
        return file

    def create(self, validated_data):
        file = validated_data.get('source')
        if file and not validated_data.get('source_type'):
            ext = file.name.lower().rsplit('.', 1)[-1]
            validated_data['source_type'] = (
                'audio' if ext in {'mp3', 'wav', 'm4a', 'aac', 'flac'}
                else 'video' if ext in {'mp4', 'webm', 'mov', 'avi', 'mkv'}
                else 'ppt'
            )
        return super().create(validated_data)
