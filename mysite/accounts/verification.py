"""
注册验证码：生成 / 限流 / 发送（邮箱 SMTP、阿里云短信）/ 校验
通道是否可用由环境变量决定（见 settings.py 末尾），未配置时：
- 本地 DEBUG：不真正发送，验证码打印到控制台并随响应返回（dev_code），方便联调
- 服务器：返回「验证码服务未配置」（503），前端凭 /auth/code-channels/ 隐藏对应选项
"""
import json
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone

from .models import VerificationCode

log = logging.getLogger(__name__)

CODE_TTL_SECONDS = 5 * 60      # 验证码有效期
RESEND_COOLDOWN = 60           # 同一目标两次发送的最小间隔
DAILY_LIMIT = 10               # 同一目标 24 小时内发送上限
MAX_ATTEMPTS = 5               # 单个验证码最大错误尝试次数


class CodeSendError(Exception):
    """发送通道故障（未配置 / SMTP 或短信接口报错），message 可直接展示给用户"""


class ThrottledError(Exception):
    """触发限流（发送过频 / 超过当日上限），message 可直接展示给用户"""


def channel_ready(channel: str) -> bool:
    """该通道是否已配置凭据（用于前后端协商展示哪个接收方式）"""
    if channel == VerificationCode.Channel.EMAIL:
        return bool(settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD)
    if channel == VerificationCode.Channel.PHONE:
        return bool(
            settings.RAG_SMS_ACCESS_KEY_ID and settings.RAG_SMS_ACCESS_KEY_SECRET
            and settings.RAG_SMS_SIGN_NAME and settings.RAG_SMS_TEMPLATE_CODE
        )
    return False


def issue_code(channel: str, target: str, purpose: str = 'register', deliver: bool = True) -> str:
    """
    生成并保存验证码；deliver=True 时真正发送。违反限流抛 ThrottledError，
    通道故障抛 CodeSendError；成功返回验证码本身。
    限流按 (通道, 目标, 用途) 维度：注册与改绑互不挤占冷却额度。
    """
    # 冷却：同一 channel+target+purpose 60s 内只允许发一次
    last = VerificationCode.objects.filter(channel=channel, target=target, purpose=purpose).first()
    if last and (wait := RESEND_COOLDOWN - int((timezone.now() - last.created_at).total_seconds())) > 0:
        raise ThrottledError(f'发送太频繁，请 {wait} 秒后再试')
    # 每日上限：防止邮箱/短信额度被刷
    sent_24h = VerificationCode.objects.filter(
        channel=channel, target=target, purpose=purpose,
        created_at__gte=timezone.now() - timedelta(hours=24),
    ).count()
    if sent_24h >= DAILY_LIMIT:
        raise ThrottledError('今日该账号的验证码发送次数已达上限，请 24 小时后再试')

    code = f'{secrets.randbelow(1_000_000):06d}'
    # 新码发出后旧码全部作废，始终只有最新一条可用
    VerificationCode.objects.filter(
        channel=channel, target=target, purpose=purpose, used=False,
    ).update(used=True)
    VerificationCode.objects.create(
        channel=channel, target=target, purpose=purpose, code=code,
        expires_at=timezone.now() + timedelta(seconds=CODE_TTL_SECONDS),
    )

    if deliver:
        if channel == VerificationCode.Channel.EMAIL:
            _send_email(target, code)
        else:
            _send_sms(target, code)
    else:
        log.warning('[验证码] 调试模式未发送 %s → %s：%s', channel, target, code)
    return code


def check_code(channel: str, target: str, code: str, purpose: str = 'register') -> None:
    """校验并消费验证码；不匹配 / 过期 / 次数用尽均抛 ValueError（message 可直接展示）"""
    with transaction.atomic():
        vc = (
            VerificationCode.objects.select_for_update()
            .filter(channel=channel, target=target, purpose=purpose, used=False)
            .first()
        )
        if vc is None or vc.is_expired or vc.is_exhausted:
            raise ValueError('验证码错误或已过期，请重新获取')
        if not secrets.compare_digest(vc.code, code):
            # 只影响这条验证码：错误次数累计到 5 次即作废
            vc.attempts += 1
            vc.save(update_fields=['attempts'])
            raise ValueError('验证码错误，请重新输入')
        vc.used = True
        vc.save(update_fields=['used', 'attempts'])


# ---------- 通道实现 ----------

def _send_email(to: str, code: str) -> None:
    """QQ / 163 等个人邮箱 SMTP（465 SSL + 授权码），未配置账号时抛错"""
    if not (settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD):
        raise CodeSendError('验证码服务未配置，请联系管理员')
    subject = '【智学纪要】注册验证码'
    text = f'您正在注册智学纪要账号，验证码：{code}，{CODE_TTL_SECONDS // 60} 分钟内有效。若非本人操作请忽略本邮件。'
    html = (
        f'<div style="font-family:system-ui,-apple-system,Segoe UI,PingFang SC,sans-serif;'
        f'max-width:420px;margin:0 auto;padding:28px;border-radius:12px;'
        f'border:1px solid #e6e9f0;color:#1a2233">'
        f'<h2 style="margin:0 0 14px;font-size:17px">智学纪要 · 注册验证码</h2>'
        f'<p style="margin:0 0 18px;color:#5a6478;font-size:14px;line-height:1.7">'
        f'您正在注册智学纪要账号，请输入以下验证码完成注册：</p>'
        f'<p style="margin:0 0 18px;font-size:30px;font-weight:700;letter-spacing:8px;color:#3b5bfd">{code}</p>'
        f'<p style="margin:0;color:#98a1b3;font-size:12.5px">'
        f'验证码 {CODE_TTL_SECONDS // 60} 分钟内有效。若非本人操作，请忽略本邮件。</p></div>'
    )
    try:
        mail = EmailMultiAlternatives(
            subject=subject, body=text,
            from_email=settings.DEFAULT_FROM_EMAIL or settings.EMAIL_HOST_USER, to=[to],
        )
        mail.attach_alternative(html, 'text/html')
        mail.send()
    except Exception as exc:  # SMTP 认证失败 / 网络等，统一转为用户可读的提示
        log.error('[验证码] 邮件发送失败 %s：%s', to, exc)
        raise CodeSendError('验证码邮件发送失败，请稍后再试') from exc


def _send_sms(to: str, code: str) -> None:
    """阿里云短信 Dysmsapi（官方 SDK），模板需含 ${code} 变量；未配置时抛错"""
    if not channel_ready(VerificationCode.Channel.PHONE):
        raise CodeSendError('验证码服务未配置，请联系管理员')
    try:
        # SDK 仅在配置了短信凭据时才会被用到，延迟导入避免无谓的启动开销
        from alibabacloud_dysmsapi20170525 import models as sms_models
        from alibabacloud_dysmsapi20170525.client import Client as SmsClient
        from alibabacloud_tea_openapi import models as open_api_models

        client = SmsClient(open_api_models.Config(
            access_key_id=settings.RAG_SMS_ACCESS_KEY_ID,
            access_key_secret=settings.RAG_SMS_ACCESS_KEY_SECRET,
            endpoint='dysmsapi.aliyuncs.com',
        ))
        response = client.send_sms(sms_models.SendSmsRequest(
            phone_numbers=to, sign_name=settings.RAG_SMS_SIGN_NAME,
            template_code=settings.RAG_SMS_TEMPLATE_CODE,
            template_param=json.dumps({'code': code}, ensure_ascii=False),
        ))
        if response.body.code != 'OK':
            log.error('[验证码] 短信发送失败 %s：%s %s', to, response.body.code, response.body.message)
            raise CodeSendError('验证码短信发送失败，请稍后再试')
    except CodeSendError:
        raise
    except Exception as exc:
        log.error('[验证码] 短信通道异常 %s：%s', to, exc)
        raise CodeSendError('验证码短信发送失败，请稍后再试') from exc
