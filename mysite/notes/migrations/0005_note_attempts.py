"""纪要流水线 worker 化：新增 attempts 尝试计数

流水线从 gunicorn 请求进程里的守护线程改为常驻 note_worker 进程领取执行；
执行者崩溃（部署重启 / OOM）遗留的 processing 态靠心跳停滞检测重新领取，
attempts 限制自动重试上限，防止坏材料无限重跑。
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notes', '0004_note_fail_reason'),
    ]

    operations = [
        migrations.AddField(
            model_name='note',
            name='attempts',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='处理尝试次数'),
        ),
    ]
