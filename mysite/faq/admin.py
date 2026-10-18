"""Django 后台注册：常见问题 / 使用技巧（调试查看数据）"""
from django.contrib import admin

from .models import Faq, Tip


@admin.register(Faq)
class FaqAdmin(admin.ModelAdmin):
    list_display = ['id', 'scope', 'short_q', 'order', 'updated_at']
    list_filter = ['scope']
    search_fields = ['q', 'a']
    ordering = ['scope', 'order']

    @admin.display(description='问题标题')
    def short_q(self, obj):
        return obj.q[:40]


@admin.register(Tip)
class TipAdmin(admin.ModelAdmin):
    list_display = ['id', 'scope', 'short_text', 'order', 'updated_at']
    list_filter = ['scope']
    search_fields = ['text']
    ordering = ['scope', 'order']

    @admin.display(description='技巧内容')
    def short_text(self, obj):
        return obj.text[:40]
