"""
账号模型：自定义用户（Django AbstractUser 扩展）+ 注册验证码
分权模型与两端前端约定一致：
  role —— user 普通用户 / admin 管理员 / super 超级管理员
  status —— active 正常 / disabled 已停用（仅禁止登录，数据保留）
统一登录：所有角色共用用户端首页登录表单，登录后由前端按角色分流
（普通用户进用户端；管理员先进入管理端工作台）
"""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    class Role(models.TextChoices):
        USER = 'user', '普通用户'
        ADMIN = 'admin', '管理员'
        SUPER = 'super', '超级管理员'

    class Status(models.TextChoices):
        ACTIVE = 'active', '正常'
        DISABLED = 'disabled', '已停用'

    # AbstractUser 自带的 email 不唯一，这里覆写为唯一（注册 / 登录均使用）
    email = models.EmailField('邮箱', unique=True)
    phone = models.CharField('手机号', max_length=11, unique=True)
    role = models.CharField('角色', max_length=10, choices=Role.choices, default=Role.USER)
    status = models.CharField('状态', max_length=10, choices=Status.choices, default=Status.ACTIVE)
    avatar = models.ImageField('头像', upload_to='avatars/', blank=True, null=True)
    created_at = models.DateTimeField('注册时间', auto_now_add=True)

    class Meta:
        db_table = 'accounts_user'
        verbose_name = '账号'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.username}（{self.get_role_display()}）'

    @property
    def is_admin_role(self) -> bool:
        return self.role in (self.Role.ADMIN, self.Role.SUPER)


class VerificationCode(models.Model):
    """注册验证码：邮箱 / 短信二选一发送，同一目标重新发送时旧码作废"""

    class Channel(models.TextChoices):
        EMAIL = 'email', '邮箱'
        PHONE = 'phone', '手机号'

    channel = models.CharField('发送通道', max_length=10, choices=Channel.choices, db_index=True)
    target = models.CharField('接收目标（邮箱统一小写）', max_length=254)
    code = models.CharField('6 位验证码', max_length=6)
    attempts = models.PositiveSmallIntegerField('已错误尝试次数', default=0)
    used = models.BooleanField('已使用', default=False)
    created_at = models.DateTimeField('发送时间', auto_now_add=True)
    expires_at = models.DateTimeField('过期时间')

    class Meta:
        db_table = 'accounts_verification_code'
        verbose_name = '注册验证码'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [models.Index(fields=['channel', 'target'])]

    def __str__(self):
        return f'{self.get_channel_display()} → {self.target}'

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_exhausted(self) -> bool:
        return self.attempts >= 5
