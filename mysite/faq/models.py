"""
FAQ 常见问题 / 使用技巧模型：scope 区分展示位置（用户端使用说明 / 管理端使用说明 / 用户端首页），
管理端工作台可维护（超级管理员可维护全部，管理员可维护用户端与首页内容）。
"""
from django.db import models


class ScopedItem(models.Model):
    """FAQ 与使用技巧的公共字段：展示端 + 排序"""

    class Scope(models.TextChoices):
        USER = 'user', '用户端'
        ADMIN = 'admin', '管理端'
        HOME = 'home', '首页'

    scope = models.CharField('展示端', max_length=6, choices=Scope.choices, default=Scope.USER, db_index=True)
    order = models.PositiveIntegerField('排序（小的在前）', default=0)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        abstract = True


class Faq(ScopedItem):
    q = models.CharField('问题标题', max_length=200)
    a = models.TextField('解决描述')

    class Meta:
        db_table = 'faq_faq'
        verbose_name = '常见问题'
        verbose_name_plural = verbose_name
        ordering = ['order', 'id']

    def __str__(self):
        return f'[{self.get_scope_display()}] {self.q}'


class Tip(ScopedItem):
    text = models.TextField('技巧内容')

    class Meta:
        db_table = 'faq_tip'
        verbose_name = '使用技巧'
        verbose_name_plural = verbose_name
        ordering = ['order', 'id']

    def __str__(self):
        return f'[{self.get_scope_display()}] {self.text[:20]}'
