"""FAQ / 使用技巧路由：/api/faqs|tips/* 与 /api/admin/faqs|tips/*"""
from django.urls import path

from . import views

urlpatterns = [
    path('faqs/', views.FaqListView.as_view()),
    path('admin/faqs/', views.AdminFaqListCreateView.as_view()),
    path('admin/faqs/<int:pk>/', views.AdminFaqDetailView.as_view()),
    path('tips/', views.TipListView.as_view()),
    path('admin/tips/', views.AdminTipListCreateView.as_view()),
    path('admin/tips/<int:pk>/', views.AdminTipDetailView.as_view()),
]
