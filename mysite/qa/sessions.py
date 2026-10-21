"""Database-backed conversation directory and authorized record persistence."""
from uuid import uuid4
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticated
from .models import QaSession, QaRecord
from notes.models import Note

class SessionSerializer(serializers.ModelSerializer):
    question_count = serializers.IntegerField(read_only=True)
    class Meta:
        model = QaSession
        fields = ['ref', 'title', 'note', 'pinned', 'pinned_at', 'created_at', 'updated_at', 'question_count']
        read_only_fields = ['ref', 'pinned_at', 'created_at', 'updated_at']

    def validate_note(self, note):
        if note and (note.uploader_id != self.context['request'].user.pk or note.status != Note.Status.DONE):
            raise ValidationError('纪要不存在、无权访问或尚未处理完成')
        return note

    def update(self, instance, validated_data):
        # A conversation keeps its original classroom association.
        if 'note' in validated_data and validated_data['note'] != instance.note:
            raise ValidationError('不能更改已有对话关联的纪要')
        if 'pinned' in validated_data:
            validated_data['pinned_at'] = timezone.now() if validated_data['pinned'] else None
        return super().update(instance, validated_data)

class SessionListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SessionSerializer
    def get_queryset(self):
        return QaSession.objects.filter(user=self.request.user, deleted=False).annotate(question_count=Count('records'))
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

class SessionDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SessionSerializer
    lookup_field = 'ref'
    def get_queryset(self):
        return QaSession.objects.filter(user=self.request.user, deleted=False).annotate(question_count=Count('records'))
    def perform_destroy(self, instance):
        with transaction.atomic():
            session = QaSession.objects.select_for_update().get(pk=instance.pk)
            session.deleted = True
            session.save(update_fields=['deleted', 'updated_at'])
            session.records.all().delete()


def prepare_session(user, data, resolved_note):
    ref = data.get('session_ref')
    if ref:
        # Compatibility: an older client may supply a previously unseen ref.
        existing = QaSession.objects.filter(ref=ref).first()
        if existing:
            if existing.user_id != user.pk or existing.deleted:
                raise NotFound('对话不存在或无权访问')
            if data.get('note_id') is not None and data['note_id'] != existing.note_id:
                raise ValidationError('对话与纪要不匹配')
            return existing
    session, created = QaSession.objects.get_or_create(ref=ref or str(uuid4()), defaults={
        'user': user, 'note': resolved_note if data.get('note_id') else None,
        'title': data['question'][:24],
    })
    if session.user_id != user.pk or session.deleted:
        raise NotFound('对话不存在或无权访问')
    return session


def save_answer(session, **values):
    # Serialize with delete so an in-flight answer cannot resurrect a deleted conversation.
    with transaction.atomic():
        current = QaSession.objects.select_for_update().get(pk=session.pk)
        if current.deleted:
            raise NotFound('对话已删除')
        record = QaRecord.objects.create(session=current, session_ref=current.ref, **values)
        current.save(update_fields=['updated_at'])
        return record
