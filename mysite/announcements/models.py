"""
公告与协议文档模型：
- Announcement 平台公告（管理员 / 超管发布；发布后除发布者外的注册用户登录时提醒一次，可随时撤回）
- AnnouncementRead 公告已读记录（"登录只弹一次"与侧边栏未读角标的数据来源）
- LegalDoc 《用户协议》/《隐私政策》自定义内容（仅超管维护；无记录时前端展示内置默认文案）
"""
from django.conf import settings
from django.db import models


class Announcement(models.Model):
    """平台公告：撤回后用户端不再展示，但管理端日志仍保留"""

    class Status(models.TextChoices):
        PUBLISHED = 'published', '已发布'
        WITHDRAWN = 'withdrawn', '已撤回'

    title = models.CharField('公告标题', max_length=100)
    content = models.TextField('公告内容')
    status = models.CharField('状态', max_length=12, choices=Status.choices, default=Status.PUBLISHED)
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='发布人',
        on_delete=models.SET_NULL, null=True, blank=True, related_name='announcements',
    )
    created_at = models.DateTimeField('发布时间', auto_now_add=True)
    withdrawn_at = models.DateTimeField('撤回时间', null=True, blank=True)

    class Meta:
        db_table = 'announcements_announcement'
        verbose_name = '平台公告'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.get_status_display()}] {self.title}'


class AnnouncementRead(models.Model):
    """公告已读记录：同一公告对同一用户只提醒一次"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='用户',
        on_delete=models.CASCADE, related_name='announcement_reads',
    )
    announcement = models.ForeignKey(
        Announcement, verbose_name='公告',
        on_delete=models.CASCADE, related_name='reads',
    )
    read_at = models.DateTimeField('已读时间', auto_now_add=True)

    class Meta:
        db_table = 'announcements_read'
        verbose_name = '公告已读记录'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=['user', 'announcement'], name='uniq_announcement_read'),
        ]

    def __str__(self):
        return f'{self.user_id} -> {self.announcement_id}'


class LegalDoc(models.Model):
    """协议文档自定义文本：行首 # 为小节标题，其余每行为一个段落"""

    class Key(models.TextChoices):
        TERMS = 'terms', '用户协议'
        PRIVACY = 'privacy', '隐私政策'

    key = models.CharField('文档标识', max_length=12, choices=Key.choices, unique=True)
    content = models.TextField('文档内容')
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='最近修改人',
        on_delete=models.SET_NULL, null=True, blank=True, related_name='legal_docs',
    )
    updated_at = models.DateTimeField('最近修改时间', auto_now=True)

    class Meta:
        db_table = 'announcements_legal_doc'
        verbose_name = '协议文档'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'[{self.key}] {self.updated_at:%Y-%m-%d %H:%M}'
