"""
账号序列化器：注册 / 登录 / 个人资料 / 管理端账号列表与创建
"""
import re

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .verification import check_code

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
    """注册（用户端表单）：创建普通用户并直接登录；需先获取并提交验证码"""

    name = serializers.CharField(min_length=2, max_length=20)
    phone = serializers.CharField(max_length=11)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=6, write_only=True)
    # 验证码与其接收通道：code_channel=email 时 code 必须与发到邮箱的验证码一致
    code = serializers.CharField(min_length=6, max_length=6)
    code_channel = serializers.ChoiceField(choices=['email', 'phone'])

    def validate_name(self, value):
        if User.objects.filter(username=value.strip()).exists():
            raise serializers.ValidationError('该用户名已被使用')
        return value.strip()

    def validate_phone(self, value):
        return _check_phone_unique(value)

    def validate_email(self, value):
        return _check_email_unique(value)

    def validate_code(self, value):
        return value.strip()

    def validate(self, attrs):
        # 验证码对哪个目标校验取决于接收通道（邮箱在 validate_email 已统一小写）
        target = attrs['email'] if attrs['code_channel'] == 'email' else attrs['phone']
        try:
            check_code(attrs['code_channel'], target, attrs['code'])
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return attrs

    def create(self, validated_data):
        # create_user 签名为 (username, email, password, **extra)；表单里的 name 即 username
        data = dict(validated_data)
        channel = data.pop('code_channel')
        data.pop('code')  # 校验已消费，不落库到用户表
        user = User.objects.create_user(
            username=data.pop('name'),
            email=data.pop('email'),
            password=data.pop('password'),
            role=User.Role.USER,
            **data,
        )
        return user


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
        data = dict(validated_data)
        return User.objects.create_user(
            username=data.pop('name'),
            email=data.pop('email'),
            password=data.pop('password'),
            role=User.Role.ADMIN,
            **data,
        )


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


class _ChangeContactSerializer(serializers.Serializer):
    """
    更换手机号 / 邮箱的公共基类：验证码发往账号绑定邮箱（证明是本人操作）。
    子类声明 contact 字段并在 validate_contact 里查占用。
    """

    code = serializers.CharField(min_length=6, max_length=6)

    def validate_code(self, value):
        return value.strip()

    def validate(self, attrs):
        target = (self.context['request'].user.email or '').strip().lower()
        if not target:
            raise serializers.ValidationError('当前账号未绑定邮箱，无法验证身份')
        try:
            check_code('email', target, attrs['code'], purpose=self.purpose)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return attrs


class ChangePhoneSerializer(_ChangeContactSerializer):
    """POST /api/auth/me/phone/ —— 更换手机号（验证码发到绑定邮箱）"""

    purpose = 'change_phone'
    phone = serializers.CharField(max_length=11)

    def validate_phone(self, value):
        return _check_phone_unique(value, exclude_pk=self.context['request'].user.pk)


class ChangeEmailSerializer(_ChangeContactSerializer):
    """POST /api/auth/me/email/ —— 更换邮箱（验证码发到原绑定邮箱）"""

    purpose = 'change_email'
    email = serializers.EmailField()

    def validate_email(self, value):
        return _check_email_unique(value, exclude_pk=self.context['request'].user.pk)
