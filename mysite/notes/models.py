"""
课堂纪要模型
纪要按「课程主题 / 核心知识点 / 重要定义与公式 / 重点问题」等板块组织，
每个结论可携带录音时间戳或图片页码出处（references 与前端溯源设计对应）。
TODO：接入真实 Agent 流水线（音频提取 → 抽帧 → OCR → ASR → 融合生成 → 向量入库）后，
由异步任务回写各板块内容；当前 generate_stub 提供占位实现，保证接口闭环可演示。
"""
from django.conf import settings
from django.db import models


class Note(models.Model):
    class Status(models.TextChoices):
        PROCESSING = 'processing', '处理中'
        DONE = 'done', '已完成'
        FAILED = 'failed', '处理失败'

    course = models.CharField('所属课程', max_length=100, db_index=True)
    title = models.CharField('课堂主题', max_length=200)
    duration = models.CharField('录音时长', max_length=20, blank=True, default='')
    status = models.CharField('处理状态', max_length=12, choices=Status.choices, default=Status.PROCESSING)
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='上传者',
        on_delete=models.CASCADE, related_name='notes',
    )
    source = models.FileField('原始材料', upload_to='notes/sources/', blank=True, null=True)
    source_type = models.CharField('材料类型', max_length=12, blank=True, default='')  # audio/video/ppt
    theme = models.TextField('课程主题', blank=True, default='')
    points = models.JSONField('核心知识点', default=list, blank=True)      # [{text, ref}]
    definitions = models.JSONField('重要定义与公式', default=list, blank=True)  # [{term, text, ref}]
    emphasized = models.JSONField('重点问题', default=list, blank=True)     # [str]
    transcript = models.TextField('转写原文', blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'notes_note'
        verbose_name = '课堂纪要'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.course} · {self.title}'


class NoteChunk(models.Model):
    """纪要检索片段（RAG 入库单元）：分块文本 + 向量化结果。
    embedding 以 JSON 存 1024 维 float 列表——当前规模（每篇纪要几十~几百片段）
    Python 余弦检索足够；数据量上来后平迁 pgvector（本字段换 VectorField，检索函数在 mysite/rag.py 一处替换）。
    """
    note = models.ForeignKey(
        Note, verbose_name='所属纪要',
        on_delete=models.CASCADE, related_name='chunks',
    )
    seq = models.IntegerField('片段序号', default=0)
    text = models.TextField('片段文本')
    ref = models.CharField('来源引用', max_length=100, blank=True, default='')  # 录音 / 板书 第3页 / 纪要定义…
    embedding = models.JSONField('向量', default=list, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'note_chunk'
        verbose_name = '纪要检索片段'
        verbose_name_plural = verbose_name
        ordering = ['note_id', 'seq']

    def __str__(self):
        return f'{self.note.title} #{self.seq}: {self.text[:20]}'
