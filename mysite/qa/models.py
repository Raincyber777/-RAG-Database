"""
智能问答模型
每条回答携带引用来源（references）：录音时间点 / PPT·板书页码，与前端溯源卡片对应。
TODO：接入向量检索（pgvector）后按相似度召回纪要片段，由 LLM 生成答案；
当前 demo_answer 提供基于关键词重合的占位检索，保证问答闭环可演示。
"""
from django.conf import settings
from django.db import models


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
    question = models.TextField('问题')
    answer = models.TextField('回答')
    references = models.JSONField('引用来源', default=list, blank=True)  # [{label, time, note_title}]
    followups = models.JSONField('追问建议', default=list, blank=True)  # [str] 回答下方可点击的追问
    liked = models.BooleanField('已点赞', default=False)
    created_at = models.DateTimeField('提问时间', auto_now_add=True)

    class Meta:
        db_table = 'qa_record'
        verbose_name = '问答记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username}: {self.question[:20]}'
