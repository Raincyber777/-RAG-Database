"""常驻纪要流水线 worker：循环领取 PROCESSING 纪要并执行完整流水线。

流水线与 Web 进程（gunicorn）解耦——部署重启 / gunicorn worker 回收不再打断生成中的纪要；
执行者自身崩溃遗留的处理态由心跳停滞检测自动重新领取（上限 services.MAX_ATTEMPTS 次，
耗尽后由读取路径 fail_stale_processing() 翻失败）。systemd 单元 rag-note-worker.service
常驻运行：ExecStart=/opt/rag-backend/venv/bin/python manage.py note_worker
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from notes import services

log = logging.getLogger('notes.worker')


class Command(BaseCommand):
    help = '常驻纪要流水线 worker：循环领取处理中的纪要并执行转写 / 生成流水线'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=float, default=3.0, help='无任务时的轮询间隔（秒）')
        parser.add_argument('--once', action='store_true', help='领取并处理一份后退出（调试用）')

    def handle(self, *args, **options):
        interval: float = options['interval']
        once: bool = options['once']
        self.stdout.write(f'note_worker 已启动（interval={interval}s, once={once}）')
        while True:
            close_old_connections()   # 常驻进程：睡完一轮先清掉可能已失效的连接
            try:
                note_id = services.claim_next_note()
            except Exception:
                log.exception('领取纪要失败，%ss 后重试', interval)
                note_id = None
            if note_id is not None:
                log.info('领取纪要 note=%s', note_id)
                try:
                    services.process_note(note_id)
                    log.info('纪要处理完成 note=%s', note_id)
                except Exception:
                    # process_note 内部已把可预期异常翻成 FAILED；这里兜底防止 worker 整体退出
                    log.exception('纪要处理异常 note=%s', note_id)
                if once:
                    return
                continue   # 有任务时立刻再看下一份，不睡间隔
            if once:
                self.stdout.write('无可领取的纪要，退出')
                return
            time.sleep(interval)
