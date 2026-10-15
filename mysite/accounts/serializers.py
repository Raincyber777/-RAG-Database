"""
账号序列化器：注册 / 登录 / 个人资料 / 管理端账号列表与创建
"""
import re

from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()

PHONE_RE = re.compile(r'^1[3-9]\d{9}$')


class UserSerializer(serializers.ModelSerializer):
    """账号信息：纪要数 / 问答数实时统计（仅普通用户使用）"""

    name = serializers.CharField(source='username')
    notes_count = serializers.SerializerMethodField()
    questions_count = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'name', 'phone', 'email', 'role', 'status', 'avatar',
            'notes_count', 'questions_count', 'created_at',
        ]
        read_only_fields = ['id', 'role', 'status', 'created_at']

    def get_notes_count(self, obj) -> int:
        return obj.notes.filter(status='done').count() if obj.role == 'user' else 0

    def get_questions_count(self, obj) -> int:
        return obj.qa_records.count() if obj.role == 'user' else 0

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # 注册时间只展示日期（与前端账号表格一致）
        data['created_at'] = instance.created_at.strftime('%Y-%m-%d')
        return data


def _check_phone_unique(phone: str, exclude_pk=None):
    if not PHONE_RE.match(phone or ''):
        raise serializers.ValidationError('手机号格式不正确')
    qs = User.objects.filter(phone=phone)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise serializers.ValidationError('该手机号已被其他账号使用')
    return phone


def _check_email_unique(email: str, exclude_pk=None):
    email = (email or '').strip().lower()
    qs = User.objects.filter(email=email)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise serializers.ValidationError('该邮箱已被其他账号使用')
    return email


class RegisterSerializer(serializers.Serializer):
    """注册（用户端表单）：创建普通用户并直接登录"""

    name = serializers.CharField(min_length=2, max_length=20)
    phone = serializers.CharField(max_length=11)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=6, write_only=True)

    def validate_name(self, value):
        if User.objects.filter(username=value.strip()).exists():
            raise serializers.ValidationError('该用户名已被使用')
        return value.strip()

    def validate_phone(self, value):
        return _check_phone_unique(value)

    def validate_email(self, value):
        return _check_email_unique(value)

    def create(self, validated_data):
        return User.objects.create_user(role=User.Role.USER, **validated_data)


class AdminCreateSerializer(serializers.Serializer):
    """超级管理员新建管理员账号（前端表单：用户名 / 手机号 / 邮箱 / 初始密码）"""

    name = serializers.CharField(min_length=2, max_length=20)
    phone = serializers.CharField(max_length=11)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)

    def validate_name(self, value):
        if User.objects.filter(username=value.strip()).exists():
            raise serializers.ValidationError('该用户名已被使用')
        return value.strip()

    def validate_phone(self, value):
        return _check_phone_unique(value)

    def validate_email(self, value):
        return _check_email_unique(value)

    def create(self, validated_data):
        return User.objects.create_user(role=User.Role.ADMIN, **validated_data)


class ResetPasswordSerializer(serializers.Serializer):
    """重置密码（管理端）：至少 8 位"""

    password = serializers.CharField(min_length=8, write_only=True)


class ChangePasswordSerializer(serializers.Serializer):
    """修改密码（个人设置）：验证旧密码"""

    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(min_length=6, write_only=True)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('原密码不正确')
        return value


class ProfileSerializer(serializers.Serializer):
    """个人资料修改（字段可选提交）"""

    name = serializers.CharField(min_length=2, max_length=20, required=False)
    phone = serializers.CharField(max_length=11, required=False)
    email = serializers.EmailField(required=False)
    avatar = serializers.ImageField(required=False, allow_null=True)

    def validate_name(self, value):
        qs = User.objects.filter(username=value.strip()).exclude(pk=self.context['request'].user.pk)
        if qs.exists():
            raise serializers.ValidationError('该用户名已被使用')
        return value.strip()

    def validate_phone(self, value):
        return _check_phone_unique(value, exclude_pk=self.context['request'].user.pk)

    def validate_email(self, value):
        return _check_email_unique(value, exclude_pk=self.context['request'].user.pk)
