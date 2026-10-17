"""纪要删除时问答记录级联删除（原 SET_NULL 会留下孤儿问答，管理端巡检出现无纪要条目）"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('notes', '0001_initial'),
        ('qa', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='qarecord',
            name='note',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='qa_records',
                to='notes.note',
                verbose_name='关联纪要',
            ),
        ),
    ]
