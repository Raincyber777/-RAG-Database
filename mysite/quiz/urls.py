"""题库路由：/api/quiz/* 与 /api/admin/quiz/*"""
from django.urls import path

from . import views

urlpatterns = [
    path('quiz/', views.QuizListView.as_view()),
    path('quiz/generate/', views.QuizGenerateView.as_view()),
    path('quiz/wrong/', views.WrongListView.as_view()),
    path('quiz/wrong/<int:pk>/', views.WrongDeleteView.as_view()),
    path('quiz/<int:pk>/submit/', views.QuizSubmitView.as_view()),
    path('admin/quiz/', views.AdminQuizListView.as_view()),
]
