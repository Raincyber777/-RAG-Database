import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import oss2
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from accounts.models import User
from feedback.models import Feedback
from notes.models import Note


CONFIG = dict(OSS_ACCESS_KEY_ID='offline-id', OSS_ACCESS_KEY_SECRET='offline-secret',
              OSS_BUCKET='test-bucket', OSS_ENDPOINT='https://oss-cn-beijing.aliyuncs.com',
              OSS_URL_EXPIRE_SECONDS=900, OSS_PREFIX='classroom', OSS_CONNECT_TIMEOUT=30,
              OSS_SECURITY_TOKEN='')


class FakeBucket:
    def __init__(self):
        self.objects = {}
        self.headers = {}

    def put_object(self, key, content, headers=None):
        self.objects[key] = content.read()
        self.headers[key] = headers

    def get_object(self, key):
        return io.BytesIO(self.objects[key])

    def object_exists(self, key):
        return key in self.objects

    def delete_object(self, key):
        self.objects.pop(key, None)

    def head_object(self, key):
        return SimpleNamespace(content_length=len(self.objects[key]))


@override_settings(**CONFIG)
class OSSStorageTests(SimpleTestCase):
    def test_private_upload_collision_roundtrip_delete(self):
        from mysite.media_storage import OSSMediaStorage
        bucket = FakeBucket()
        with patch('mysite.media_storage.oss2.Bucket', return_value=bucket):
            storage = OSSMediaStorage()
            a = storage.save('notes/sources/讲义.pdf', ContentFile(b'one'))
            b = storage.save('notes/sources/讲义.pdf', ContentFile(b'two'))
            self.assertNotEqual(a, b)
            self.assertTrue(a.startswith('oss/notes/sources/'))
            self.assertTrue(a.endswith('.pdf'))
            with storage.open(a) as file:
                self.assertEqual(file.read(), b'one')
            self.assertEqual(storage.size(b), 3)
            self.assertEqual(bucket.headers['classroom/' + a]['x-oss-object-acl'], 'private')
            storage.delete(a)
            self.assertFalse(storage.exists(a))
            self.assertTrue(storage.exists(b))

    def test_sdk_generates_https_expiring_signed_url_without_network(self):
        from mysite.media_storage import OSSMediaStorage
        from urllib.parse import urlsplit, parse_qs
        storage = OSSMediaStorage()
        url = urlsplit(storage.url('oss/notes/sources/example.pdf'))
        self.assertEqual(url.scheme, 'https')
        self.assertEqual(url.netloc, 'test-bucket.oss-cn-beijing.aliyuncs.com')
        self.assertIn('Signature', parse_qs(url.query))
        self.assertIn('Expires', parse_qs(url.query))
        self.assertNotIn('offline-secret', url.geturl())

    def test_missing_config_fails_instead_of_silently_saving_locally(self):
        from mysite.media_storage import OSSMediaStorage
        with override_settings(OSS_ACCESS_KEY_SECRET=''):
            with self.assertRaises(ImproperlyConfigured):
                OSSMediaStorage()

    def test_rejects_path_traversal(self):
        from mysite.media_storage import OSSMediaStorage
        storage = OSSMediaStorage()
        for name in ['../secret', '/absolute', 'oss/../../secret', 'C:/secret']:
            with self.subTest(name=name), self.assertRaises(SuspiciousFileOperation):
                storage.url(name)

    def test_local_legacy_signed_link_expiry_and_tampering(self):
        from mysite.media_storage import OSSMediaStorage
        from mysite.media_views import legacy_media
        from rest_framework.test import APIRequestFactory
        from django.http import Http404
        from urllib.parse import urlsplit, parse_qs
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            Path(directory, 'old.txt').write_bytes(b'old-file')
            storage = OSSMediaStorage()
            with storage.open('old.txt') as file:
                self.assertEqual(file.read(), b'old-file')
            with patch('django.core.signing.time.time', return_value=1000):
                token = parse_qs(urlsplit(storage.url('old.txt')).query)['token'][0]
            request = APIRequestFactory().get('/api/media/legacy/', {'token': token})
            with patch('django.core.signing.time.time', return_value=1100):
                response = legacy_media(request)
                self.assertEqual(b''.join(response.streaming_content), b'old-file')
                response.close()
            with patch('django.core.signing.time.time', return_value=2000), self.assertRaises(Http404):
                legacy_media(request)
            with self.assertRaises(Http404):
                legacy_media(APIRequestFactory().get('/api/media/legacy/', {'token': token + 'bad'}))


@override_settings(**CONFIG, STORAGES={'default': {'BACKEND': 'mysite.media_storage.OSSMediaStorage'}})
class MigrationTests(TestCase):
    def test_dry_run_then_copy_updates_all_references_and_is_repeatable(self):
        bucket = FakeBucket()
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), \
                patch('mysite.media_storage.oss2.Bucket', return_value=bucket):
            Path(directory, 'legacy.txt').write_bytes(b'old lesson')
            user = User.objects.create_user(username='owner', avatar='legacy.txt')
            note = Note.objects.create(uploader=user, title='Lesson', course='Math', source='legacy.txt')
            feedback = Feedback.objects.create(user=user, title='Issue', content='x', images=['legacy.txt'])
            call_command('migrate_media_to_oss', stdout=io.StringIO())
            note.refresh_from_db()
            self.assertEqual(note.source.name, 'legacy.txt')
            self.assertEqual(bucket.objects, {})
            call_command('migrate_media_to_oss', apply=True, stdout=io.StringIO())
            note.refresh_from_db(); user.refresh_from_db(); feedback.refresh_from_db()
            self.assertTrue(note.source.name.startswith('oss/'))
            self.assertEqual(note.source.name, user.avatar.name)
            self.assertEqual(feedback.images, [note.source.name])
            self.assertEqual(len(bucket.objects), 1)
            self.assertTrue(Path(directory, 'legacy.txt').exists())
            call_command('migrate_media_to_oss', apply=True, stdout=io.StringIO())
            self.assertEqual(len(bucket.objects), 1)
