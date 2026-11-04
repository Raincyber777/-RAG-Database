"""常驻纪要流水线 worker：循环领取 PROCESSING 纪要并执行完整流水线。

流水线与 Web 进程（gunicorn）解耦——部署重启 / gunicorn worker 回收不再打断生成中的纪要；
执行者自身崩溃遗留的处理态由心跳停滞检测自动重新领取（上限 services.MAX_ATTEMPTS 次，
耗尽后由读取路径 fail_stale_processing() 翻失败）。systemd 单元 rag-note-worker.service
常驻运行：ExecStart=/opt/rag-backend/venv/bin/python manage.py note_worker

优雅退出：部署重启时 systemd 先发 SIGTERM，worker 把当前纪要处理完再退出
（TimeoutStopSec=1800 给足 30 分钟）——正常情况下自动重试机制只兜底真正的崩溃。
"""
import logging
import signal
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
        # SIGTERM/SIGINT 只置退出标志：正在跑的纪要处理完才退出（部署不杀生成中的流水线）
        draining = {'stop': False}

        def request_stop(signum, frame):
            draining['stop'] = True
            log.warning('收到信号 %s，处理完当前纪要后退出（drain）', signum)

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        self.stdout.write(f'note_worker 已启动（interval={interval}s, once={once}）')
        while not draining['stop']:
            close_old_connections()   # 常驻进程：睡完一轮先清掉可能已失效的连接
            try:
                claim = services.claim_next_note()
            except Exception:
                log.exception('领取纪要失败，%ss 后重试', interval)
                claim = None
            if claim is None:
                if once:
                    self.stdout.write('无可领取的纪要，退出')
                    return
                time.sleep(interval)
                continue
            note_id, attempt_no = claim
            started = time.monotonic()
            log.info('领取纪要 note=%s 第%d次尝试', note_id, attempt_no)
            try:
                services.process_note(note_id)
                log.info('纪要处理结束 note=%s 耗时%.0fs', note_id, time.monotonic() - started)
            except Exception:
                # process_note 内部已把可预期异常翻成 FAILED；这里兜底防止 worker 整体退出
                log.exception('纪要处理异常 note=%s', note_id)
            if once:
                return
