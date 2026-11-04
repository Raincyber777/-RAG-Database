"""纪要流水线阶段打点：新增 last_phase 字段

流水线各阶段（上传转写 / 等待转写 / 生成纪要 / 向量化入库…）随心跳记录到纪要上，
卡死或重试耗尽翻失败时，失败原因能直接带出最后停留的阶段，线上排查不再盲猜。
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notes', '0005_note_attempts'),
    ]

    operations = [
        migrations.AddField(
            model_name='note',
            name='last_phase',
            field=models.CharField(blank=True, default='', max_length=30, verbose_name='最近流水线阶段'),
        ),
    ]
