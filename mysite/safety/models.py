"""内容安全模型：用户健康档案（本人可见）+ 敏感内容审计日志（仅管理端，内容脱敏）。"""
from django.conf import settings
from django.db import models


class HealthProfile(models.Model):
    """用户健康档案：问答中提到过敏史 / 用药 / 病史 / 饮食禁忌时自动结构化提取入库。
    字段级权限：仅本人经 /api/user/profile/ 读写；管理端无明文接口（只见脱敏审计日志）。
    涉及饮食 / 用药 / 健康建议时检索本档案，命中禁忌项强制阻断（mysite/moderation.py）。"""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, verbose_name='用户',
        on_delete=models.CASCADE, related_name='health_profile',
    )
    allergies = models.JSONField('过敏史', default=list, blank=True)    # [{item, raw, created_at}]
    medications = models.JSONField('正在用药', default=list, blank=True)
    conditions = models.JSONField('病史', default=list, blank=True)
    diet_taboos = models.JSONField('饮食禁忌', default=list, blank=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'health_profile'
        verbose_name = '健康档案'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'{self.user.username} 的健康档案'

    def items(self):
        """全部禁忌条目的扁平视图（item 小写去重），供禁忌比对。"""
        out = []
        for section in (self.allergies, self.medications, self.conditions, self.diet_taboos):
            for it in section or []:
                item = str(it.get('item') or '').strip()
                if item:
                    out.append(item)
        return out

    def merge(self, extracted: dict) -> bool:
        """合并一次抽取结果（{allergies/medications/conditions/diet_taboos: [str]}）；
        按 item 归一化去重，新条目带 {item, raw, created_at}。有新增返回 True。"""
        from django.utils import timezone
        changed = False
        now = timezone.localtime().strftime('%Y-%m-%d %H:%M')
        for field in ('allergies', 'medications', 'conditions', 'diet_taboos'):
            existing = {str(it.get('item') or '').strip().lower() for it in getattr(self, field)}
            got = getattr(self, field)
            for raw in (extracted.get(field) or []):
                item = str(raw or '').strip()
                if not item or item.lower() in existing:
                    continue
                got.append({'item': item, 'raw': str(raw), 'created_at': now})
                existing.add(item.lower())
                changed = True
        return changed


class SensitiveAuditLog(models.Model):
    """敏感内容审计日志：拦截 / 引导 / 健康建档都记一笔；question 与 detail 均为脱敏后内容。
    命中审核的对话不进训练样本池（训练选材按 QaRecord.moderation / 本表双重排除）。"""
    class Kind(models.TextChoices):
        HEALTH_EXTRACT = 'health_extract', '健康建档'
        HEALTH_BLOCK = 'health_block', '禁忌拦截'
        POLITICAL = 'political', '政治引导'
        BLOCKED = 'blocked', '违规拦截'

    class Action(models.TextChoices):
        EXTRACT = 'extract', '建档'
        REDIRECT = 'redirect', '引导'
        BLOCK = 'block', '拦截'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='用户',
        on_delete=models.SET_NULL, related_name='sensitive_audit_logs', null=True, blank=True,
    )
    kind = models.CharField('类型', max_length=14, choices=Kind.choices, db_index=True)
    question = models.TextField('内容（脱敏）')
    action = models.CharField('处理', max_length=10, choices=Action.choices)
    detail = models.JSONField('命中详情', default=list, blank=True)  # 脱敏后的类别/词条
    created_at = models.DateTimeField('时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'sensitive_audit_log'
        verbose_name = '敏感审核日志'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [models.Index(fields=['kind', 'created_at'])]

    def __str__(self):
        return f'[{self.get_kind_display()}] {self.question[:20]}'
