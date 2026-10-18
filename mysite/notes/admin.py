"""Django 后台注册：课堂纪要与检索片段（调试查看数据）"""
from django.contrib import admin

from .models import Note, NoteChunk


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ['id', 'course', 'title', 'uploader', 'source_type', 'status', 'duration', 'created_at']
    list_filter = ['status', 'source_type', 'course']
    search_fields = ['title', 'theme', 'course', 'uploader__username']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(NoteChunk)
class NoteChunkAdmin(admin.ModelAdmin):
    list_display = ['id', 'note', 'seq', 'ref', 'created_at']
    list_filter = ['note__course']
    search_fields = ['text', 'note__title']
