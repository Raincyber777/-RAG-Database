"""Django 后台注册（便于调试查看数据）"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'phone', 'role', 'status', 'created_at']
    list_filter = ['role', 'status']
    search_fields = ['username', 'email', 'phone']
    fieldsets = BaseUserAdmin.fieldsets + (
        ('账号属性', {'fields': ('phone', 'role', 'status', 'avatar')}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('账号属性', {'fields': ('phone', 'email', 'role', 'status')}),
    )
