"""数据迁移：创建评审 / 授课教师专用超级管理员账号 teacher。

随 CI 自动 migrate 在生产与本地同时生效。安全约定：
- 迁移文件只存 PBKDF2 哈希，不存明文密码；明文随提交材料中的「评审测试账号」信息交付；
- update_or_create 以 username 为键：账号已存在时对其做接管（重置为教师凭据），
  邮箱 / 手机号若被其他账号占用则自动加后缀避让，避免迁移中途唯一约束报错。

Co-Authored-By: Claude Code <noreply@anthropic.com>
"""
from django.db import migrations

PASSWORD_HASH = 'pbkdf2_sha256$1000000$feZr8JUiG3m9dA5b7QK5yb$CESLZKP7nSliTB7nRO37PU/gwePTZdWiJvloosgLb48='
PREFERRED_EMAIL = 'teacher@zhixuejiyao.cn'
PREFERRED_PHONE = '13800138000'


def create_teacher(apps, schema_editor):
    User = apps.get_model('accounts', 'User')

    email, phone = PREFERRED_EMAIL, PREFERRED_PHONE
    # 邮箱 / 手机号被其他账号占用时加后缀避让，保证迁移不因唯一约束失败
    for i in range(1, 20):
        if not User.objects.filter(email=email).exclude(username='teacher').exists():
            break
        email = f'teacher{i}@zhixuejiyao.cn'
    for i in range(1, 20):
        if not User.objects.filter(phone=phone).exclude(username='teacher').exists():
            break
        phone = f'1380013800{i:02d}'[-11:]

    User.objects.update_or_create(
        username='teacher',
        defaults={
            'email': email,
            'phone': phone,
            'role': 'super',
            'password': PASSWORD_HASH,
            'is_active': True,
            'is_staff': True,
        },
    )
    # status 沿模型默认值，不在迁移里干预


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_alter_verificationcode_options_and_more'),
    ]

    operations = [
        migrations.RunPython(create_teacher, migrations.RunPython.noop),
    ]
