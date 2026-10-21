from django.urls import path

from .views import AdminAuditLogListView, HealthProfileView

urlpatterns = [
    path('user/profile/', HealthProfileView.as_view()),
    path('admin/audit/logs/', AdminAuditLogListView.as_view()),
]
