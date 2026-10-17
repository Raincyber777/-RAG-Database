"""
纪要生成流水线（真实版）：上传材料 → 按类型抽取内容 → LLM 融合生成纪要板块 → 向量化入库
- audio/video：DashScope paraformer-v2 文件转写（本地文件上传临时 OSS → 异步任务轮询 →
  句子级时间戳），转写行带 [mm:ss] 前缀，供 RAG 引用对齐到录音时间点
- image：qwen-vl-plus 识别板书 / PPT 截图
- doc：txt / md 直接读取；ppt / pdf / docx 等暂不支持（状态置为 failed）
- 抽取失败 / LLM 失败：状态置为 failed，接口层不阻塞（全程后台线程）
"""
import base64
import logging
import mimetypes
import os
import re
import threading
import time
import uuid

import requests
from django.conf import settings

log = logging.getLogger(__name__)

from mysite import llm, rag
from notes.models import Note

ASR_BASE = 'https://dashscope.aliyuncs.com'
ASR_MODEL = 'paraformer-v2'
ASR_POLL_INTERVAL = 3      # 秒
ASR_POLL_MAX = 400         # 最长轮询 20 分钟（一节课录音量级）
TEXT_MAX = 20000           # 文本材料截断长度


class UnsupportedSource(Exception):
    """材料类型暂不支持自动生成"""


def _mmss(milliseconds: int) -> str:
    total = max(0, int(milliseconds)) // 1000
    return f'{total // 60:02d}:{total % 60:02d}'


def _upload_to_oss(file_path: str) -> str:
    """本地文件上传到 DashScope 临时 OSS，返回 oss:// 引用（录音文件识别要求可访问 URL）"""
    filename = f'{uuid.uuid4().hex}-{os.path.basename(file_path)}'
    res = requests.get(
        f'{ASR_BASE}/api/v1/uploads',
        params={'action': 'getPolicy', 'model': ASR_MODEL},
        headers={'Authorization': f'Bearer {settings.DASHSCOPE_API_KEY}'},
        timeout=30,
    )
    res.raise_for_status()
    pol = res.json()['data']
    # upload_dir 不带尾部斜杠（形如 dashscope-instant/<uid>/<date>/<task-uuid>），需显式补 /
    obj_key = pol['upload_dir'].rstrip('/') + '/' + filename
    form = {
        'OSSAccessKeyId': pol['oss_access_key_id'],
        'Signature': pol['signature'],
        'policy': pol['policy'],
        'x-oss-object-acl': pol.get('x_oss_object_acl', 'private'),
        'x-oss-forbid-overwrite': pol.get('x_oss_forbid_overwrite', 'true'),
        'key': obj_key,
        'success_action_status': '200',
    }
    with open(file_path, 'rb') as f:
        res = requests.post(pol['upload_host'], data=form, files={'file': f}, timeout=300)
    res.raise_for_status()
    return 'oss://' + obj_key


def _asr_sentences(file_path: str) -> list[tuple[str, str]]:
    """paraformer-v2 文件转写，返回 [(mm:ss, 句子文本)]"""
    oss_url = _upload_to_oss(file_path)
    res = requests.post(
        f'{ASR_BASE}/api/v1/services/audio/asr/transcription',
        headers={
            'Authorization': f'Bearer {settings.DASHSCOPE_API_KEY}',
            'X-DashScope-OssResourceResolve': 'enable',
            'X-DashScope-Async': 'enable',
        },
        json={'model': ASR_MODEL,
              'input': {'file_urls': [oss_url]},
              'parameters': {'language_hints': ['zh']}},
        timeout=60,
    )
    res.raise_for_status()
    task_id = res.json()['output']['task_id']
    for _ in range(ASR_POLL_MAX):
        time.sleep(ASR_POLL_INTERVAL)
        out = requests.get(f'{ASR_BASE}/api/v1/tasks/{task_id}',
                           headers={'Authorization': f'Bearer {settings.DASHSCOPE_API_KEY}'},
                           timeout=30).json().get('output', {})
        status_ = out.get('task_status')
        if status_ == 'SUCCEEDED':
            break
        if status_ == 'FAILED':
            raise RuntimeError(f'转写任务失败：{out.get("message") or "ASR task failed"}')
    else:
        raise TimeoutError('转写任务超时')
    result = out['results'][0]
    url = result.get('transcription_url') or (result.get('output') or {}).get('file_url')
    data = requests.get(url, timeout=60).json()
    sentences = (data.get('transcripts') or [{}])[0].get('sentences') or []
    return [(_mmss(s.get('begin_time', 0)), s.get('text', '').strip()) for s in sentences if s.get('text', '').strip()]


def _vision_extract(file_path: str) -> str:
    """qwen-vl-plus 识别板书 / PPT 图片，转写出文字内容"""
    mime = mimetypes.guess_type(file_path)[0] or 'image/jpeg'
    with open(file_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    text = llm.chat([{
        'role': 'user',
        'content': [
            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}},
            {'type': 'text', 'text': '这是一张课堂板书或 PPT 照片。请完整转写图中的文字内容（含标题、公式、批注），'
                                     '按内容分条输出纯文本，不要添加图中没有的解释。'},
        ],
    }], model=llm.MODEL_VL, temperature=0.1)
    if not text:
        raise RuntimeError('图片识别失败')
    return text.strip()


def _read_text(file_path: str) -> str:
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read(TEXT_MAX).strip()


def _extract(note: Note) -> tuple[str, str]:
    """按材料类型抽取内容，返回 (内容文本, 来源标签)"""
    if not note.source:
        raise UnsupportedSource('未上传材料文件')
    path = note.source.path
    kind = note.source_type or 'audio'
    if kind in ('audio', 'video'):
        sentences = _asr_sentences(path)
        if not sentences:
            raise RuntimeError('转写结果为空')
        return '\n'.join(f'[{ts}] {text}' for ts, text in sentences), '录音'
    if kind == 'image':
        return _vision_extract(path), '板书'
    if kind == 'doc':
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.txt', '.md'):
            return _read_text(path), '讲义'
    raise UnsupportedSource('该格式暂不支持自动生成纪要，请上传 音频 / 视频 / 图片 / TXT / MD')


def _summarize(content: str, source_label: str) -> dict:
    """qwen-plus 融合生成纪要板块；时间戳（录音材料）对齐进 ref，供引用溯源"""
    system = (
        '你是课堂纪要编辑。根据给定的课堂材料内容生成结构化纪要，只输出 JSON：'
        '{"theme": "一句话课程主题",'
        ' "points": [{"text": "核心知识点", "ts": "mm:ss"}],'
        ' "definitions": [{"term": "术语", "text": "解释", "ts": "mm:ss"}],'
        ' "emphasized": ["老师强调或布置的重点问题"]}'
        '要求：忠实于材料内容，不要编造材料里没有的信息；'
        'points 4~8 条、definitions 2~6 条、emphasized 1~4 条；'
        '材料行首带 [mm:ss] 时间戳时，ts 必须取材料中真实存在的时间点；没有时间戳则省略 ts。'
    )
    data = llm.chat_json(
        [{'role': 'system', 'content': system},
         {'role': 'user', 'content': f'课堂材料（来源：{source_label}）：\n{content}'}],
        temperature=0.3,
    )
    if not isinstance(data, dict):
        raise RuntimeError('纪要生成失败（模型输出异常）')

    stamps = set(re.findall(r'\[(\d{1,2}:\d{2})\]', content))
    has_ts = source_label == '录音' and bool(stamps)

    def _ref(ts) -> str:
        return f'录音 {ts}' if has_ts and ts in stamps else source_label

    points = []
    for p in (data.get('points') or []):
        if isinstance(p, dict) and p.get('text'):
            points.append({'text': str(p['text']).strip(), 'ref': _ref(p.get('ts'))})
        elif isinstance(p, str) and p.strip():
            points.append({'text': p.strip(), 'ref': source_label})
    definitions = []
    for d in (data.get('definitions') or []):
        if isinstance(d, dict) and d.get('term') and d.get('text'):
            definitions.append({'term': str(d['term']).strip(), 'text': str(d['text']).strip(), 'ref': _ref(d.get('ts'))})
    emphasized = [str(x).strip() for x in (data.get('emphasized') or []) if str(x).strip()]
    return {
        'theme': str(data.get('theme') or '').strip(),
        'points': points,
        'definitions': definitions,
        'emphasized': emphasized,
    }


def process_note(note_id: int) -> None:
    """流水线主体（后台线程执行）：抽取 → 生成 → 入库；失败置为 failed"""
    note = Note.objects.filter(pk=note_id).first()
    if note is None:
        return
    try:
        content, source_label = _extract(note)
        fields = _summarize(content, source_label)
        note.theme = fields['theme'] or note.title
        note.points = fields['points']
        note.definitions = fields['definitions']
        note.emphasized = fields['emphasized']
        note.transcript = content
        if source_label == '录音':
            stamps = re.findall(r'^\[(\d{1,2}:\d{2})\]', content, flags=re.M)
            note.duration = stamps[-1] if stamps else note.duration  # 末句时间 ≈ 时长
        note.status = Note.Status.DONE
        note.save()
        rag.index_note(note)  # 生成完成即向量化入库，供智能问答检索
    except Exception:
        log.exception('纪要流水线失败 note=%s', note_id)
        note.status = Note.Status.FAILED
        note.save(update_fields=['status', 'updated_at'])


def start_processing(note: Note) -> None:
    """置为处理中并启动后台流水线线程（接口立即返回，前端轮询状态）"""
    note.status = Note.Status.PROCESSING
    note.save(update_fields=['status', 'updated_at'])
    threading.Thread(target=process_note, args=(note.pk,), daemon=True).start()
