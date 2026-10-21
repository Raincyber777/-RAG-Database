"""Remote-storage regressions; never connect to OSS or a model service."""
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from feedback.models import Feedback
from feedback.serializers import FeedbackSerializer
from notes.models import Note
from notes.services import _extract


class RemoteStorage(Storage):
    def __init__(self):
        self.objects = {}

    def exists(self, name):
        return name in self.objects

    def _save(self, name, content):
        self.objects[name] = content.read()
        return name

    def _open(self, name, mode='rb'):
        return ContentFile(self.objects[name], name=name)

    def delete(self, name):
        self.objects.pop(name, None)

    def url(self, name):
        return 'https://private.example/' + name + '?signature=temporary'


@override_settings(STORAGES={'default': {'BACKEND': 'notes.test_oss.RemoteStorage'}})
class RemoteMaterialTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='test-only')

    def note(self, name='lecture.txt', body=b'Lesson from object storage', kind='doc'):
        return Note.objects.create(uploader=self.user, title='Lesson', course='Math',
                                   source=ContentFile(body, name=name), source_type=kind)

    def test_remote_text_extraction_keeps_actual_content(self):
        self.assertEqual(_extract(self.note()), ('Lesson from object storage', '讲义'))

    def test_pdf_and_ppt_dispatch_keep_suffix_and_cleanup_after_parser_failure(self):
        for suffix, kind in [('.pdf', 'doc'), ('.pptx', 'ppt')]:
            paths = []
            def parser(path):
                paths.append(Path(path))
                self.assertEqual(Path(path).suffix, suffix)
                self.assertEqual(Path(path).read_bytes(), b'fixture')
                raise ValueError('parser failed')
            with self.subTest(suffix=suffix), patch('notes.services._read_doc', side_effect=parser):
                with self.assertRaisesRegex(ValueError, 'parser failed'):
                    _extract(self.note('lecture' + suffix, b'fixture', kind))
                self.assertTrue(paths)
                self.assertFalse(paths[0].exists())

    def test_remote_audio_and_image_pass_readable_file_and_cleanup(self):
        for kind, suffix, parser, result, expected in [
            ('audio', '.wav', '_asr_sentences', [('00:01', 'hello')], ('[00:01] hello', '录音')),
            ('image', '.png', '_vision_extract', 'board', ('board', '板书')),
        ]:
            paths = []
            def read_file(path):
                paths.append(Path(path))
                self.assertEqual(Path(path).read_bytes(), b'fixture')
                return result
            with self.subTest(kind=kind), patch('notes.services.' + parser, side_effect=read_file):
                self.assertEqual(_extract(self.note('material' + suffix, b'fixture', kind)), expected)
                self.assertFalse(paths[0].exists())

    def test_feedback_returns_storage_url_even_without_request(self):
        feedback = Feedback.objects.create(user=self.user, title='Issue', content='details',
                                           images=['feedback/screen.png'])
        expected = 'https://private.example/feedback/screen.png?signature=temporary'
        self.assertEqual(FeedbackSerializer(feedback).data['images'], [expected])

    def test_api_upload_returns_private_url_and_other_user_cannot_read_or_delete(self):
        client = APIClient()
        client.force_authenticate(self.user)
        with patch('notes.views.start_processing'):
            response = client.post('/api/notes/', {'course': 'Math', 'title': 'Lesson',
                                   'source': ContentFile(b'hello', name='lesson.txt')}, format='multipart')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['source'].startswith('https://private.example/'))
        pk = response.data['id']
        stranger = User.objects.create_user(username='stranger', phone='13900000002', email='stranger@example.test')
        client.force_authenticate(stranger)
        self.assertEqual(client.get(f'/api/notes/{pk}/').status_code, 403)
        self.assertEqual(client.delete(f'/api/notes/{pk}/').status_code, 403)
        client.force_authenticate(self.user)
        note = Note.objects.get(pk=pk)
        name, storage = note.source.name, note.source.storage
        self.assertEqual(client.delete(f'/api/notes/{pk}/').status_code, 204)
        self.assertFalse(storage.exists(name))
