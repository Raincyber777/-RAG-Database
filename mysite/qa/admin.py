"""Django 后台注册：问答记录（调试查看数据）"""
from django.contrib import admin

from .models import QaRecord


@admin.register(QaRecord)
class QaRecordAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'note', 'short_question', 'liked', 'created_at']
    list_filter = ['liked', 'note__course']
    search_fields = ['question', 'answer', 'user__username']
    readonly_fields = ['created_at']

    @admin.display(description='问题')
    def short_question(self, obj):
        return obj.question[:40]
