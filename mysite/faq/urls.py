"""FAQ 路由：/api/faqs/* 与 /api/admin/faqs/*"""
from django.urls import path

from . import views

urlpatterns = [
    path('faqs/', views.FaqListView.as_view()),
    path('admin/faqs/', views.AdminFaqListCreateView.as_view()),
    path('admin/faqs/<int:pk>/', views.AdminFaqDetailView.as_view()),
]
