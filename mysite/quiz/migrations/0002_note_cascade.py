"""纪要删除时练习题级联删除（原 SET_NULL 会留下孤儿题，管理端题库出现已删纪要的条目）"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('notes', '0001_initial'),
        ('quiz', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='quizitem',
            name='note',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='quiz_items',
                to='notes.note',
                verbose_name='来源纪要',
            ),
        ),
    ]
