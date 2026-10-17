"""为已完成纪要构建检索片段并向量化入库（RAG 预计算）。

用法：
  python manage.py embed_notes            # 处理全部已完成纪要
  python manage.py embed_notes --note 3   # 只处理指定纪要
需要配置 DASHSCOPE_API_KEY；上传/重新生成纪要后的异步管线中也应调用 index_note。
"""
from django.core.management.base import BaseCommand, CommandError

from mysite import llm, rag
from notes.models import Note


class Command(BaseCommand):
    help = 'RAG 入库：为已完成纪要重建检索片段并调用向量模型（text-embedding-v4）'

    def add_arguments(self, parser):
        parser.add_argument('--note', type=int, default=None, help='只处理指定纪要 id')

    def handle(self, *args, **options):
        if not llm.available():
            raise CommandError('未配置 DASHSCOPE_API_KEY，无法向量化（见 mysite/local_settings.py）')
        qs = Note.objects.filter(status=Note.Status.DONE)
        if options['note']:
            qs = qs.filter(pk=options['note'])
        total_chunks = 0
        for note in qs:
            chunks = rag.chunk_note(note)
            if not rag.index_note(note):
                raise CommandError(f'纪要《{note.title}》向量化失败，请检查网络与 key 配额')
            total_chunks += len(chunks)
            self.stdout.write(f'《{note.title}》 片段 x{len(chunks)} 已入库')
        self.stdout.write(self.style.SUCCESS(f'完成：{qs.count()} 篇纪要 / {total_chunks} 个片段已向量化'))
