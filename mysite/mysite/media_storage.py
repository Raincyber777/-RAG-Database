"""Private OSS storage. Persist object names, never expiring URLs or credentials."""
import mimetypes
import posixpath
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import oss2
from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible

LEGACY_SALT = 'classroom.legacy-media'


def safe_name(name):
    name = str(name).replace('\\', '/')
    if (not name or name.startswith('/') or ':' in name or '\x00' in name
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise SuspiciousFileOperation('Invalid media object name')
    return name


@deconstructible
class OSSMediaStorage(Storage):
    """New names start with oss/; pre-existing names remain local until migrated."""

    def __init__(self):
        required = ('OSS_ACCESS_KEY_ID', 'OSS_ACCESS_KEY_SECRET', 'OSS_BUCKET', 'OSS_ENDPOINT')
        missing = [key for key in required if not getattr(settings, key, '')]
        if missing:
            raise ImproperlyConfigured('Missing OSS settings: ' + ', '.join(missing))
        endpoint = urlsplit(settings.OSS_ENDPOINT)
        if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.path not in ('', '/')
                or endpoint.query or endpoint.fragment or endpoint.username or endpoint.port):
            raise ImproperlyConfigured('OSS_ENDPOINT must be an HTTPS regional endpoint')
        self.prefix = getattr(settings, 'OSS_PREFIX', 'classroom').strip('/')
        if self.prefix:
            safe_name(self.prefix)
        self.expires = int(getattr(settings, 'OSS_URL_EXPIRE_SECONDS', 900))
        if not 60 <= self.expires <= 3600:
            raise ImproperlyConfigured('OSS_URL_EXPIRE_SECONDS must be between 60 and 3600')
        token = getattr(settings, 'OSS_SECURITY_TOKEN', '')
        auth = (oss2.StsAuth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET, token)
                if token else oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET))
        self.bucket = oss2.Bucket(auth, settings.OSS_ENDPOINT, settings.OSS_BUCKET,
                                  connect_timeout=getattr(settings, 'OSS_CONNECT_TIMEOUT', 30))
        self.local = FileSystemStorage(location=settings.MEDIA_ROOT, base_url=settings.MEDIA_URL)

    def _key(self, name):
        return '/'.join(filter(None, (self.prefix, safe_name(name))))

    def get_available_name(self, name, max_length=None):
        name = safe_name(name)
        # UUID names prevent cross-user filename collisions and overwrite races.
        directory = posixpath.dirname(name)
        if directory == 'oss' or directory.startswith('oss/'):
            directory = directory[4:] if directory.startswith('oss/') else ''
        suffix = posixpath.splitext(name)[1].lower()
        if not re.fullmatch(r'\.[a-z0-9]{1,12}', suffix):
            suffix = ''
        result = posixpath.join('oss', directory, uuid.uuid4().hex + suffix)
        if max_length and len(result) > max_length:
            raise SuspiciousFileOperation('Media object name exceeds field length')
        return result

    def _save(self, name, content):
        content.seek(0)
        self.bucket.put_object(self._key(name), content, headers={
            'x-oss-object-acl': 'private',
            'x-oss-forbid-overwrite': 'true',
            'Content-Type': mimetypes.guess_type(name)[0] or 'application/octet-stream',
            'Cache-Control': 'private, max-age=0, no-store',
        })
        return name

    def _open(self, name, mode='rb'):
        name = safe_name(name)
        if mode not in ('r', 'rb'):
            raise ValueError('Use storage.save() to write OSS objects')
        if not name.startswith('oss/'):
            return self.local.open(name, mode)
        # Large material spills to disk; never load a whole recording into RAM.
        temp = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b')
        try:
            result = self.bucket.get_object(self._key(name))
            try:
                shutil.copyfileobj(result, temp, length=1024 * 1024)
            finally:
                result.close()
            temp.seek(0)
            return File(temp, name=name)
        except BaseException:
            temp.close()
            raise

    def exists(self, name):
        name = safe_name(name)
        return (self.bucket.object_exists(self._key(name)) if name.startswith('oss/')
                else self.local.exists(name))

    def size(self, name):
        name = safe_name(name)
        return (self.bucket.head_object(self._key(name)).content_length if name.startswith('oss/')
                else self.local.size(name))

    def delete(self, name):
        name = safe_name(name)
        if name.startswith('oss/'):
            self.bucket.delete_object(self._key(name))
        else:
            self.local.delete(name)

    def url(self, name):
        name = safe_name(name)
        if name.startswith('oss/'):
            return self.bucket.sign_url('GET', self._key(name), self.expires)
        token = signing.dumps(name, salt=LEGACY_SALT)
        return '/api/media/legacy/?' + urlencode({'token': token})

    def path(self, name):
        name = safe_name(name)
        if name.startswith('oss/'):
            raise NotImplementedError('OSS objects have no local path')
        return self.local.path(name)


@contextmanager
def local_material_path(field):
    """Give existing parsers a filename; release temporary files even on failure."""
    try:
        path = field.path
    except NotImplementedError:
        path = None
    if path is not None:
        yield path
        return
    with tempfile.TemporaryDirectory(prefix='classroom-material-') as directory:
        path = Path(directory) / ('material' + Path(field.name).suffix.lower())
        with field.storage.open(field.name, 'rb') as source, path.open('wb') as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        yield str(path)
