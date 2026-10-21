"""问答路由：/api/qa/* 与 /api/admin/qa/*"""
from django.urls import path

from . import views
from .sessions import SessionListView, SessionDetailView

urlpatterns = [
    path('qa/sessions/', SessionListView.as_view()),
    path('qa/sessions/<str:ref>/', SessionDetailView.as_view()),
    path('qa/', views.QaListView.as_view()),
    path('qa/ask/', views.AskView.as_view()),
    path('qa/ask/stream/', views.AskStreamView.as_view()),
    path('qa/suggestions/', views.SuggestionsView.as_view()),
    path('qa/<int:pk>/like/', views.QaLikeView.as_view()),
    path('admin/qa/', views.AdminQaListView.as_view()),
]
