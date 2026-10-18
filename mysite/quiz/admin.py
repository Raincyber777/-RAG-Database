"""Django 后台注册：题库与错题本（调试查看数据）"""
from django.contrib import admin

from .models import QuizItem, WrongRecord


@admin.register(QuizItem)
class QuizItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'course', 'chapter', 'type', 'difficulty', 'short_stem', 'uploader', 'created_at']
    list_filter = ['type', 'difficulty', 'course']
    search_fields = ['stem', 'knowledge', 'course', 'uploader__username']
    readonly_fields = ['created_at']

    @admin.display(description='题干')
    def short_stem(self, obj):
        return obj.stem[:40]


@admin.register(WrongRecord)
class WrongRecordAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'quiz', 'short_answer', 'created_at']
    search_fields = ['user__username', 'quiz__stem']

    @admin.display(description='我的作答')
    def short_answer(self, obj):
        return (obj.my_answer or '')[:30]
