"""公告 / 协议文档路由（经 mysite/urls.py 的 path('api/', include(...)) 挂载）"""
from django.urls import path

from . import views

urlpatterns = [
    path('announcements/', views.AnnouncementListView.as_view()),
    path('announcements/unread/', views.AnnouncementUnreadView.as_view()),
    path('announcements/read/', views.AnnouncementReadView.as_view()),
    path('admin/announcements/', views.AdminAnnouncementListCreateView.as_view()),
    path('admin/announcements/batch_delete/', views.AdminAnnouncementBatchDeleteView.as_view()),
    path('admin/announcements/<int:pk>/withdraw/', views.AdminAnnouncementWithdrawView.as_view()),
    path('admin/announcements/<int:pk>/', views.AdminAnnouncementDetailView.as_view()),
    path('legal/<str:key>/', views.LegalDocPublicView.as_view()),
    path('admin/legal/<str:key>/', views.AdminLegalDocView.as_view()),
]
