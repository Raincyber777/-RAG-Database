"""Django 后台注册：问答记录 / 反馈埋点 / 训练样本 / 知识库缺口（调试查看数据）"""
from django.contrib import admin

from .models import KnowledgeGap, QaFeedback, QaRecord


@admin.register(QaRecord)
class QaRecordAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'note', 'short_question', 'liked', 'moderation', 'created_at']
    list_filter = ['liked', 'moderation', 'note__course']
    search_fields = ['question', 'answer', 'user__username']
    readonly_fields = ['created_at']

    @admin.display(description='问题')
    def short_question(self, obj):
        return obj.question[:40]


@admin.register(QaFeedback)
class QaFeedbackAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'record', 'action', 'value', 'created_at']
    list_filter = ['action']
    search_fields = ['user__username', 'value']


@admin.register(KnowledgeGap)
class KnowledgeGapAdmin(admin.ModelAdmin):
    list_display = ['id', 'short_question', 'count', 'status', 'updated_at']
    list_filter = ['status']

    @admin.display(description='问题')
    def short_question(self, obj):
        return obj.question[:40]
