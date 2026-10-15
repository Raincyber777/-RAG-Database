"""问题反馈路由：/api/feedback/* 与 /api/admin/feedbacks/*"""
from django.urls import path

from . import views

urlpatterns = [
    path('feedback/', views.FeedbackListCreateView.as_view()),
    path('admin/feedbacks/', views.AdminFeedbackListView.as_view()),
    path('admin/feedbacks/<int:pk>/resolve/', views.AdminFeedbackResolveView.as_view()),
]
