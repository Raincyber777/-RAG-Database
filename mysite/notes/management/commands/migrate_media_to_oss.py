"""Copy referenced local media, then atomically update references. Originals stay."""
from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction

from feedback.models import Feedback
from notes.models import Note
from mysite.media_storage import OSSMediaStorage, safe_name


class Command(BaseCommand):
    help = 'Preview local media migration; --apply copies to OSS and updates references (keep originals).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        User = get_user_model()
        names = set(Note.objects.exclude(source='').exclude(source=None).values_list('source', flat=True))
        names.update(User.objects.exclude(avatar='').exclude(avatar=None).values_list('avatar', flat=True))
        for images in Feedback.objects.values_list('images', flat=True).iterator():
            names.update(images or [])
        names = sorted(safe_name(name) for name in names if name and not name.startswith('oss/'))
        self.stdout.write(f'{len(names)} local media objects; apply={options["apply"]}')
        local = FileSystemStorage(location=settings.MEDIA_ROOT)
        missing = [name for name in names if not local.exists(name)]
        if missing:
            raise CommandError(f'{len(missing)} referenced local files are missing; restore them before migration.')
        if not options['apply'] or not names:
            return
        if settings.STORAGES['default']['BACKEND'] != 'mysite.media_storage.OSSMediaStorage':
            raise CommandError('Enable RAG_MEDIA_STORAGE=oss before applying migration.')
        storage = OSSMediaStorage()
        for name in names:
            with local.open(name, 'rb') as file:
                new_name = storage.save(name, file, max_length=100)
            if storage.size(new_name) != local.size(name):
                storage.delete(new_name)
                raise CommandError('Uploaded file size mismatch; database references were not changed.')
            try:
                with transaction.atomic():
                    Note.objects.filter(source=name).update(source=new_name)
                    User.objects.filter(avatar=name).update(avatar=new_name)
                    for feedback in Feedback.objects.select_for_update().iterator():
                        if name in feedback.images:
                            feedback.images = [new_name if p == name else p for p in feedback.images]
                            feedback.save(update_fields=['images'])
            except Exception:
                storage.delete(new_name)
                raise
            self.stdout.write('Migrated one object (local original retained).')
