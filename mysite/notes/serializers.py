"""课堂纪要序列化器"""
from rest_framework import serializers

from .models import Note


class NoteSerializer(serializers.ModelSerializer):
    uploader_name = serializers.CharField(source='uploader.username', read_only=True)
    # 该纪要名下的问答条数（用户端问答页会话角标用）
    questions = serializers.SerializerMethodField()

    class Meta:
        model = Note
        fields = [
            'id', 'course', 'title', 'duration', 'status', 'uploader', 'uploader_name',
            'source', 'source_type', 'theme', 'points', 'definitions', 'emphasized',
            'transcript', 'questions', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'status', 'uploader', 'theme', 'points', 'definitions',
                            'emphasized', 'transcript', 'questions', 'created_at', 'updated_at']

    def get_questions(self, obj) -> int:
        return obj.qa_records.count()


class NoteCreateSerializer(serializers.ModelSerializer):
    """创建纪要：课程 + 主题 + 可选材料文件；材料类型按扩展名识别
    白名单与流水线能力一一对应（前端注册表见用户端 utils/uploadFormats.ts）"""

    AUDIO_EXT = {'mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg'}
    VIDEO_EXT = {'mp4', 'webm', 'mov', 'avi', 'mkv'}
    IMAGE_EXT = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
    PPT_EXT = {'pptx', 'ppsx'}                       # 老版二进制 .ppt 无法解析，拒绝并提示另存
    DOC_EXT = {'pdf', 'docx', 'txt', 'md', 'markdown', 'xlsx', 'csv', 'zip'}  # zip 内嵌文档逐个解析；老版 .doc / .xls 拒绝

    ALLOWED_EXT = AUDIO_EXT | VIDEO_EXT | IMAGE_EXT | PPT_EXT | DOC_EXT

    # 各类型大小上限（MB）：录音/视频受 DashScope 转写 2GB 约束留裕量；
    # 图片经服务端压缩后送 qwen-vl（单图含 base64 ≤10MB），放宽上传、后端压缩
    KIND_LIMITS = {
        'audio': ('录音', 500),
        'video': ('视频', 1024),
        'image': ('图片', 20),
        'ppt': ('PPT', 100),
        'doc': ('文档', 100),
    }

    class Meta:
        model = Note
        fields = ['course', 'title', 'duration', 'source', 'source_type']

    def validate_source(self, file):
        if file is None:
            return file
        name = (file.name or '').lower()
        ext = name.rsplit('.', 1)[-1] if '.' in name else ''
        if ext in ('ppt', 'pps'):
            raise serializers.ValidationError('老版 .ppt / .pps 暂不支持，请在 Office 中另存为 .pptx 后上传')
        if ext in ('doc', 'xls'):
            raise serializers.ValidationError('老版 .doc / .xls 暂不支持，请在 Office 中另存为 .docx / .xlsx 后上传')
        if ext not in self.ALLOWED_EXT:
            raise serializers.ValidationError('不支持的材料格式（录音 / 视频 / 图片 / PPT / PDF / Word / 文本 / 压缩包）')
        label, mb = self.KIND_LIMITS[self._kind_of(ext)]
        if file.size and file.size > mb * 1024 * 1024:
            raise serializers.ValidationError(f'{label}文件过大：最大支持 {mb}MB，请压缩后再上传')
        return file

    @staticmethod
    def _kind_of(ext: str) -> str:
        if ext in NoteCreateSerializer.AUDIO_EXT:
            return 'audio'
        if ext in NoteCreateSerializer.VIDEO_EXT:
            return 'video'
        if ext in NoteCreateSerializer.IMAGE_EXT:
            return 'image'
        if ext in NoteCreateSerializer.PPT_EXT:
            return 'ppt'
        return 'doc'

    def create(self, validated_data):
        file = validated_data.get('source')
        if file and not validated_data.get('source_type'):
            ext = file.name.lower().rsplit('.', 1)[-1]
            validated_data['source_type'] = self._kind_of(ext)
        return super().create(validated_data)
