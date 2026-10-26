"""课堂纪要路由：/api/notes/* 与 /api/admin/notes/*"""
from django.urls import path

from . import views

urlpatterns = [
    path('notes/', views.NoteListCreateView.as_view()),
    path('notes/course-rename/', views.CourseRenameView.as_view()),
    path('notes/course-delete/', views.CourseDeleteView.as_view()),
    path('notes/<int:pk>/', views.NoteDetailView.as_view()),
    path('notes/<int:pk>/reprocess/', views.NoteReprocessView.as_view()),
    path('admin/notes/', views.AdminNoteListView.as_view()),
    path('admin/notes/batch_delete/', views.AdminNoteBatchDeleteView.as_view()),
]
