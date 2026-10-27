"""
智能问答模型
每条回答携带引用来源（references）：录音时间点 / PPT·板书页码，与前端溯源卡片对应。
QaRecord 即对话日志：除问答本体与引用外，还落召回片段（retrieval）、改写问题、
审核标记（moderation）等进化原料；反馈埋点与知识缺口自动沉淀，无需人工审核环节。
"""
import uuid
from django.conf import settings
from django.db import models


class QaSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='qa_sessions')
    ref = models.CharField(max_length=64, default=uuid.uuid4, unique=True)
    title = models.CharField(max_length=200, default='新的对话')
    note = models.ForeignKey('notes.Note', null=True, blank=True, on_delete=models.SET_NULL)
    pinned = models.BooleanField(default=False)
    pinned_at = models.DateTimeField(null=True, blank=True)
    deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-pinned', '-pinned_at', '-updated_at', '-id']


class QaRecord(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='提问者',
        on_delete=models.CASCADE, related_name='qa_records',
    )
    note = models.ForeignKey(
        'notes.Note', verbose_name='关联纪要',
        on_delete=models.CASCADE, related_name='qa_records',
        null=True, blank=True,  # 通用问答（账号尚无已完成纪要，纯外部检索回答）为空
    )
    session = models.ForeignKey(QaSession, null=True, blank=True, on_delete=models.CASCADE, related_name='records')
    question = models.TextField('问题')
    answer = models.TextField('回答')
    images = models.JSONField(default=list, blank=True)  # private storage paths, never signed URLs
    references = models.JSONField('引用来源', default=list, blank=True)  # [{label, time, note_title}]
    followups = models.JSONField('追问建议', default=list, blank=True)  # [str] 回答下方可点击的追问
    # 前端自由对话的会话标识（'qa'+时间戳）：刷新后按会话拉回历史；课内追问走 note 关联，此字段留空
    session_ref = models.CharField('前端会话标识', max_length=64, blank=True, default='', db_index=True)
    liked = models.BooleanField('已点赞', default=False)
    # ---- 对话进化原料（训练样本 / 检索调优 / 展示）----
    question_rewrite = models.CharField('改写后的问题', max_length=500, blank=True, default='')
    # 召回片段与相似度：[{chunk_id, score, ref}] top-k；反馈闭环据此升降 NoteChunk.weight
    retrieval = models.JSONField('召回片段', default=list, blank=True)
    used_external = models.BooleanField('使用了外部知识', default=False)
    profile_updated = models.BooleanField('本次更新了健康档案', default=False)  # done 事件带给前端提示
    # 审核标记：''/political(政治引导)/health_block(禁忌拦截)；非空的不进训练样本池
    moderation = models.CharField('审核标记', max_length=20, blank=True, default='')
    created_at = models.DateTimeField('提问时间', auto_now_add=True)

    class Meta:
        db_table = 'qa_record'
        verbose_name = '问答记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username}: {self.question[:20]}'


class QaFeedback(models.Model):
    """按消息的反馈埋点：点赞 / 点踩 / 复制 / 重新提问 / 停留时长，驱动片段权重与样本筛选。"""
    class Action(models.TextChoices):
        LIKE = 'like', '点赞'
        DISLIKE = 'dislike', '点踩'
        COPY = 'copy', '复制'
        REASK = 'reask', '重新提问'
        DWELL = 'dwell', '停留'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='用户',
        on_delete=models.CASCADE, related_name='qa_feedbacks',
    )
    record = models.ForeignKey(
        QaRecord, verbose_name='问答记录',
        on_delete=models.CASCADE, related_name='feedbacks',
    )
    action = models.CharField('反馈类型', max_length=10, choices=Action.choices, db_index=True)
    # 附加值：dwell 存停留秒数；reask 存重新提问的新问题；其余留空
    value = models.CharField('附加值', max_length=200, blank=True, default='')
    created_at = models.DateTimeField('反馈时间', auto_now_add=True)

    class Meta:
        db_table = 'qa_feedback'
        verbose_name = '问答反馈'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['record', 'action']),
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        return f'{self.user.username} {self.get_action_display()} #{self.record_id}'


class KnowledgeGap(models.Model):
    """知识库缺口：反复出现却答不出依据的问题，提示管理员补充课堂材料。"""
    class Status(models.TextChoices):
        OPEN = 'open', '待补充'
        RESOLVED = 'resolved', '已补充'
        IGNORED = 'ignored', '已忽略'

    key = models.CharField('聚合键', max_length=100, db_index=True)  # 规范化问题前 50 字
    question = models.TextField('问题示例')
    count = models.PositiveIntegerField('出现次数', default=1)
    sample_record = models.ForeignKey(
        QaRecord, verbose_name='示例问答', on_delete=models.SET_NULL,
        related_name='knowledge_gaps', null=True, blank=True,
    )
    status = models.CharField('状态', max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_at = models.DateTimeField('首次出现', auto_now_add=True)
    updated_at = models.DateTimeField('最近出现', auto_now=True)

    class Meta:
        db_table = 'knowledge_gap'
        verbose_name = '知识库缺口'
        verbose_name_plural = verbose_name
        ordering = ['-count', '-updated_at']

    def __str__(self):
        return f'{self.question[:20]}（×{self.count}）'
