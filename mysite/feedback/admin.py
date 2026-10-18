"""Django 后台注册：问题反馈（调试查看数据）"""
from django.contrib import admin

from .models import Feedback


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'user', 'contact', 'status', 'created_at']
    list_filter = ['status']
    search_fields = ['title', 'content', 'user__username']
    readonly_fields = ['created_at']
