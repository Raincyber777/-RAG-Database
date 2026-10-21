from uuid import uuid4
from django.db import migrations


def backfill(apps, schema_editor):
    Record = apps.get_model('qa', 'QaRecord')
    Session = apps.get_model('qa', 'QaSession')
    alias = schema_editor.connection.alias
    groups = {}
    for record in Record.objects.using(alias).filter(session__isnull=True).order_by('created_at', 'pk').iterator():
        group = (record.user_id, record.session_ref or ('note:' + str(record.note_id) if record.note_id else 'general'))
        session = groups.get(group)
        if session is None:
            ref = record.session_ref or str(uuid4())
            if Session.objects.using(alias).filter(ref=ref).exists():
                ref = str(uuid4())
            session = Session.objects.using(alias).create(user_id=record.user_id, ref=ref,
                note_id=record.note_id if not record.session_ref else None,
                title=record.question[:24] if record.session_ref else '历史问答')
            Session.objects.using(alias).filter(pk=session.pk).update(created_at=record.created_at)
            groups[group] = session
        Record.objects.using(alias).filter(pk=record.pk).update(session_id=session.pk, session_ref=session.ref)
        Session.objects.using(alias).filter(pk=session.pk).update(updated_at=record.created_at)


class Migration(migrations.Migration):
    dependencies = [('qa', '0006_qasession_qarecord_session')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
