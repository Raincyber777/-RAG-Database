"""
FAQ 常见问题模型：每条由「问题标题 + 解决描述」组成；
scope 区分展示在用户端还是管理端的使用说明页，管理端工作台可维护。
"""
from django.db import models


class Faq(models.Model):
    class Scope(models.TextChoices):
        USER = 'user', '用户端'
        ADMIN = 'admin', '管理端'

    scope = models.CharField('展示端', max_length=6, choices=Scope.choices, default=Scope.USER, db_index=True)
    q = models.CharField('问题标题', max_length=200)
    a = models.TextField('解决描述')
    order = models.PositiveIntegerField('排序（小的在前）', default=0)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'faq_faq'
        verbose_name = '常见问题'
        verbose_name_plural = verbose_name
        ordering = ['order', 'id']

    def __str__(self):
        return f'[{self.get_scope_display()}] {self.q}'
