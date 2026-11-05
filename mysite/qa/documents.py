"""聊天文档附件：解析文本并围绕文档流式作答（与 images.py 的视觉问答同构）。
复用 notes.services 的文档解析器（pdf/docx/pptx/xlsx/csv/txt/md），解析在请求进程内完成，
不落存储——文档内容只进当次提问的上下文，不随记录持久化。"""
import logging
import os
import tempfile

from contextlib import closing
from django.db import transaction
from rest_framework.exceptions import ValidationError

from mysite import llm
from notes.services import _read_doc

from .sessions import save_answer

log = logging.getLogger(__name__)
MAX_DOC_BYTES = 20 * 1024 * 1024
MAX_TOTAL_DOC_BYTES = 60 * 1024 * 1024
# 单文档注入上下文的字符上限：超长讲义截断，保证首字延迟可控
DOC_TEXT_LIMIT = 12000

# 与 notes 解析器能力对齐（zip 让用户走「课堂纪要」整包流水线，聊天里不拆包）
DOC_EXTS = ('.pdf', '.docx', '.pptx', '.ppsx', '.xlsx', '.csv', '.txt', '.md', '.markdown')


def extract_doc_text(file) -> tuple[str, str]:
    """单个附件 → (名称, 文本)。格式 / 大小校验在此，解析失败统一转 ValidationError。"""
    name = str(file.name).replace('\\', '/').rsplit('/', 1)[-1][:200]
    ext = os.path.splitext(name)[1].lower()
    if ext not in DOC_EXTS:
        raise ValidationError(f'暂不支持文档格式 {ext or name}：请上传 PDF / Word / PPT / 文本类文件')
    if file.size > MAX_DOC_BYTES:
        raise ValidationError(f'单个文档不能超过 20MB：{name}')
    file.seek(0)
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        for chunk in file.chunks():
            tmp.write(chunk)
        tmp.close()
        text = (_read_doc(tmp.name) or '').strip()
    except ValidationError:
        raise
    except Exception as exc:
        log.warning('QA doc parse failed (%s): %s', type(exc).__name__, name)
        raise ValidationError(f'文档读取失败：{name}，请确认文件未损坏后重试') from None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    if not text:
        raise ValidationError(f'无法从文档中读取到文字内容：{name}')
    if len(text) > DOC_TEXT_LIMIT:
        text = text[:DOC_TEXT_LIMIT] + '……（内容过长，已截断）'
    return name, text


_DOC_RULES = (
    '你是「智学纪要」的课堂助教。学生上传了文档并围绕它提问，请依据给出的文档内容用中文回答：'
    '1）优先依据文档内容作答，关键结论注明出自哪份文档（如（来自《xx.docx》））；'
    '2）文档不足以回答的部分，可结合你的知识补充并注明「补充自模型知识」，不要编造文档里没有的内容；'
    '3）不要声称进行过联网检索，不要输出与文档无关的免责声明。'
)


def doc_events(session, user, note, question, docs, *, profile_updated=False, answer_suffix=''):
    """文档问答事件流：status → delta… → done（含问答记录）。事件形状与 image_events 一致，
    调用方（AskView / AskStreamView）无需区分图片与文档。"""
    from .serializers import QaRecordSerializer
    try:
        yield 'status', {'text': '正在阅读文档'}
        blocks, refs = [], []
        for name, text in docs:
            blocks.append(f'《{name}》\n{text}')
            refs.append({'label': name, 'time': '', 'detail': '上传文档', 'kind': 'doc'})
        messages = [
            {'role': 'system', 'content': _DOC_RULES},
            {'role': 'user', 'content':
                '学生上传的文档内容如下：\n\n' + '\n\n'.join(blocks) + f'\n\n学生问题：{question}'},
        ]
        parts: list[str] = []
        stream = llm.chat_stream(messages, temperature=0.3)
        if stream is None:
            raise RuntimeError('LLM stream unavailable')
        with closing(stream) as gen:
            for text in gen:
                parts.append(text)
                yield 'delta', {'text': text}
        answer = ''.join(parts).strip()
        if not answer:
            raise RuntimeError('Empty doc answer')
        if answer_suffix and '不构成医疗建议' not in answer:
            answer += answer_suffix
            yield 'delta', {'text': answer_suffix}
        with transaction.atomic():
            record = save_answer(session, user=user, note=note, question=question,
                                 answer=answer, references=refs, followups=[],
                                 profile_updated=profile_updated)
            payload = QaRecordSerializer(record).data
        yield 'done', payload
    except ValidationError:
        raise
    except Exception as exc:
        log.warning('Q&A doc processing failed (%s)', type(exc).__name__)
        yield 'replace', {'text': ''}
        yield 'error', {'detail': '文档阅读或保存失败，请稍后重试；也可能是文档格式暂不支持解析。'}
