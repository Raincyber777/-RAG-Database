"""
问题反馈模型：与用户端「问题反馈」表单一致 —— 标题 + 详细描述 + 可选截图；
管理员在管理端查看详情与截图并标记已处理。
"""
from django.conf import settings
from django.db import models


class Feedback(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', '待处理'
        RESOLVED = 'resolved', '已处理'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='提交人',
        on_delete=models.CASCADE, related_name='feedbacks',
    )
    title = models.CharField('标题', max_length=120)
    content = models.TextField('详细描述')
    contact = models.CharField('联系方式', max_length=100, blank=True, default='')
    images = models.JSONField('截图', default=list, blank=True)  # [media 路径, ...]
    status = models.CharField('状态', max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField('提交时间', auto_now_add=True)

    class Meta:
        db_table = 'feedback_feedback'
        verbose_name = '问题反馈'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title}（{self.get_status_display()}）'
