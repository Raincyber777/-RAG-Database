from django.contrib import admin

from .models import HealthProfile, SensitiveAuditLog


@admin.register(HealthProfile)
class HealthProfileAdmin(admin.ModelAdmin):
    """健康档案仅在调试后台可见（生产管理端走 /api/admin/audit/logs/ 的脱敏日志）。"""
    list_display = ['id', 'user', 'updated_at']
    search_fields = ['user__username']


@admin.register(SensitiveAuditLog)
class SensitiveAuditLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'kind', 'action', 'short_question', 'created_at']
    list_filter = ['kind', 'action']
    search_fields = ['question', 'user__username']

    @admin.display(description='内容')
    def short_question(self, obj):
        return obj.question[:30]
