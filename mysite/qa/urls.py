"""问答路由：/api/qa/* 与 /api/admin/qa/*、训练样本 /api/admin/training/*"""
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
    path('qa/<int:pk>/feedback/', views.QaFeedbackView.as_view()),
    path('qa/rollback/', views.QaRollbackView.as_view()),
    path('admin/qa/', views.AdminQaListView.as_view()),
    path('admin/qa/batch_delete/', views.AdminQaBatchDeleteView.as_view()),
    path('admin/kb/gaps/', views.AdminKbGapListView.as_view()),
    path('admin/kb/gaps/<int:pk>/<str:action>/', views.KbGapActionView.as_view()),
    path('admin/training/generate/', views.TrainingGenerateView.as_view()),
    path('admin/training/samples/export/', views.TrainingSampleExportView.as_view()),
    path('admin/training/samples/', views.TrainingSampleListView.as_view()),
    path('admin/training/samples/<int:pk>/review/', views.TrainingSampleReviewView.as_view()),
]
