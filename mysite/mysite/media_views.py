"""Compatibility downloads for pre-OSS files; links are short-lived bearer tokens."""
from django.conf import settings
from django.core import signing
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage
from django.http import FileResponse, Http404
from django.views.decorators.http import require_safe

from .media_storage import LEGACY_SALT, safe_name


@require_safe
def legacy_media(request):
    try:
        name = safe_name(signing.loads(request.GET.get('token', ''), salt=LEGACY_SALT,
                                      max_age=getattr(settings, 'OSS_URL_EXPIRE_SECONDS', 900)))
        if name.startswith('oss/'):
            raise Http404
        storage = FileSystemStorage(location=settings.MEDIA_ROOT)
        response = FileResponse(storage.open(name, 'rb'))
    except (signing.BadSignature, SuspiciousFileOperation, OSError, TypeError, ValueError):
        raise Http404('File link is invalid or expired') from None
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Referrer-Policy'] = 'no-referrer'
    return response
