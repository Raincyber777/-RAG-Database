"""数据迁移：创建评审专用演示账号 student（普通用户）与 manager（管理员）。

随 CI 自动 migrate 在生产与本地同时生效，与 0004 teacher 同一套安全约定：
- 迁移文件只存 PBKDF2 哈希，不存明文密码；明文随提交材料中的「在线演示与测试账号」交付；
- update_or_create 以 username 为键；邮箱 / 手机号被其他账号占用时自动加后缀避让；
- 删除类 / 账号管理类操作由 mysite.utils.ensure_not_demo 闸门统一限制，保护演示数据。
"""
from django.db import migrations

ACCOUNTS = [
    # (用户名, 邮箱, 手机号, 角色, 密码哈希, is_staff)
    ('student', 'student@zhixuejiyao.cn', '13800138001', 'user',
     'pbkdf2_sha256$1000000$XtiqLv4ogOXJTVFMlComt9$VJUjELswSN7XStU7Fu9qFv5RknOgZgjKWo9O4G3h9ww=', False),
    ('manager', 'manager@zhixuejiyao.cn', '13800138002', 'admin',
     'pbkdf2_sha256$1000000$islDQdDX2LP4hei8XDH3pr$t6aui8A67d7ZlbaRxvMpslDHIPIrJHnUmjRG1cGt1q8=', True),
]


def create_demo_accounts(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for username, email, phone, role, password_hash, is_staff in ACCOUNTS:
        # 邮箱 / 手机号被其他账号占用时加后缀避让，保证迁移不因唯一约束失败
        for i in range(1, 20):
            if not User.objects.filter(email=email).exclude(username=username).exists():
                break
            email = email.replace('@', f'{i}@', 1)
        for i in range(1, 20):
            if not User.objects.filter(phone=phone).exclude(username=username).exists():
                break
            phone = f'1380013800{i:02d}'[-11:]
        User.objects.update_or_create(
            username=username,
            defaults={
                'email': email,
                'phone': phone,
                'role': role,
                'password': password_hash,
                'is_active': True,
                'is_staff': is_staff,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_teacher_superuser'),
    ]

    operations = [
        migrations.RunPython(create_demo_accounts, migrations.RunPython.noop),
    ]
