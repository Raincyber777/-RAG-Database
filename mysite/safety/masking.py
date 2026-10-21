"""脱敏工具：进入审计日志的内容一律先过这里，避免敏感信息明文留存。
档案本体（HealthProfile）是功能数据不脱敏——只有日志侧遮蔽。"""
import re

_PHONE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
_EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_ID = re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')


def mask_contact(text: str) -> str:
    """手机号 / 邮箱 / 身份证打码。"""
    if not text:
        return text
    text = _PHONE.sub(lambda m: m.group(0)[:3] + '****' + m.group(0)[-2:], text)
    text = _EMAIL.sub(lambda m: m.group(0)[:2] + '***@***', text)
    text = _ID.sub(lambda m: m.group(0)[:4] + '***********' + m.group(0)[-3:], text)
    return text


def mask_item(item: str) -> str:
    """健康词条遮蔽：保留首尾各 1 字，中间打码（「青霉素」→「青**素」），保留类别可读性。"""
    item = (item or '').strip()
    if len(item) <= 2:
        return item[0] + '*' if item else item
    return item[0] + '*' * (len(item) - 2) + item[-1]


def mask_health_detail(items: list[str]) -> list[str]:
    """审计日志 detail 用：类别可保留，词条遮蔽。"""
    return [mask_item(x) for x in items or []]
