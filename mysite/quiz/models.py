"""
题库模型：基于纪要自动出题（也可由管理端导入），作答后即时批改；
错题自动进入错题本，答对后移出。与用户端「自创题练习」对应。
出题由 LLM 按纪要片段生成（见 quiz.views.QuizGenerateView），题目带难度、
来源引用（可对齐到录音时间点）与知识点标签，供前端筛选与溯源展示。
"""
from django.conf import settings
from django.db import models


class QuizItem(models.Model):
    class Type(models.TextChoices):
        SINGLE = 'single', '单选'
        MULTIPLE = 'multiple', '多选'
        INDEFINITE = 'indefinite', '不定项'
        JUDGE = 'judge', '判断'
        BLANK = 'blank', '填空'
        ANSWER = 'answer', '简答'

    note = models.ForeignKey(
        'notes.Note', verbose_name='来源纪要',
        on_delete=models.CASCADE, related_name='quiz_items',
    )
    course = models.CharField('所属课程', max_length=100, db_index=True)
    chapter = models.CharField('章节', max_length=100, blank=True, default='')
    type = models.CharField('题型', max_length=12, choices=Type.choices)
    difficulty = models.CharField(
        '难度', max_length=10, blank=True, default='中等',
        choices=[('简单', '简单'), ('中等', '中等'), ('困难', '困难')],
    )
    stem = models.TextField('题干')
    options = models.JSONField('选项', default=list, blank=True)  # ['A. ...', ...]
    answer = models.TextField('参考答案')
    analysis = models.TextField('解析', blank=True, default='')
    cite = models.CharField('来源引用', max_length=50, blank=True, default='')  # 如「录音 02:13」
    knowledge = models.CharField('知识点标签', max_length=100, blank=True, default='')
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='出题人',
        on_delete=models.CASCADE, related_name='quiz_items',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'quiz_item'
        verbose_name = '练习题'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.get_type_display()}] {self.stem[:20]}'


class WrongRecord(models.Model):
    """错题本：同一题只保留最近一次错误记录，答对后移除"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='用户',
        on_delete=models.CASCADE, related_name='wrong_records',
    )
    quiz = models.ForeignKey(QuizItem, verbose_name='题目', on_delete=models.CASCADE, related_name='wrong_records')
    my_answer = models.TextField('我的作答', blank=True, default='')
    created_at = models.DateTimeField('收录时间', auto_now_add=True)

    class Meta:
        db_table = 'quiz_wrong_record'
        verbose_name = '错题记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'quiz'], name='uniq_user_quiz_wrong'),
        ]

    def __str__(self):
        return f'{self.user.username} 错题 #{self.quiz_id}'
