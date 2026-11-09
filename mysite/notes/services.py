"""
纪要生成流水线（真实版）：上传材料 → 按类型抽取内容 → LLM 融合生成纪要板块 → 向量化入库
- audio/video：DashScope paraformer-v2 文件转写（本地文件上传临时 OSS → 异步任务轮询 →
  句子级时间戳），转写行带 [mm:ss] 前缀，供 RAG 引用对齐到录音时间点
- image：qwen-vl-plus 识别板书 / PPT 截图
- doc / ppt：txt / md 直读；pdf（PyMuPDF）、docx（python-docx）、pptx / ppsx（python-pptx）、
  xlsx（openpyxl）、csv 按扩展名分派解析；老版二进制 .ppt / .doc / .xls 在序列化层即拒绝
- 抽取失败 / LLM 失败：状态置为 failed，接口层不阻塞（流水线由常驻 note_worker 进程领取执行，
  与 gunicorn 生命周期解耦：部署重启 / worker 回收不再打断生成中的纪要）
"""
import base64
import glob
import hashlib
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import timedelta

import requests
from django.conf import settings
from django.db import close_old_connections
from django.db.models import F, Q
from django.utils import timezone

log = logging.getLogger(__name__)

from mysite import llm, rag
from mysite.media_storage import local_material_path
from mysite.moderation import moderate
from safety.masking import mask_contact
from safety.models import SensitiveAuditLog
from notes.models import Note

ASR_BASE = 'https://dashscope.aliyuncs.com'
ASR_MODEL = 'paraformer-v2'
ASR_POLL_INTERVAL = 3      # 秒
ASR_POLL_MAX = 600         # 最长轮询 30 分钟（一节课录音量级；worker 常驻不再怕部署重启，上限放宽）
NO_SPEECH_TEXT = ('未在材料中识别到可用的语音内容或可读的画面文字：请确认音视频里有人声讲解'
                  '或清晰的板书 / 幻灯片（纯音乐 / 纯画面 / 静音材料无法生成纪要），可更换材料后重试。')
KEYFRAME_MAX = 10             # 无人声视频抽帧上限（场景切换优先，退化按间隔采样）
KEYFRAME_SCENE_THRESHOLD = 0.2   # ffmpeg 场景切换判定阈值：幻灯片 / 板书翻页会被选中
TEXT_MAX = 60000           # 文本材料保留的最大字符数（RAG 入库与转写原文共用）
SUMMARIZE_DIRECT_MAX = 15000   # 不超过该长度直接单次总结；超出走分段 map-reduce
SUMMARIZE_CHUNK = 12000        # map-reduce 每段目标长度（按段落边界切）
ZIP_INNER_MAX = 20 * 1024 * 1024    # 压缩包内单文件解压上限（对齐图片上限量级）
ZIP_TOTAL_MAX = 200 * 1024 * 1024   # 压缩包解压后总量上限（防压缩炸弹）


def _is_empty_frame_text(text: str) -> bool:
    """纯色 / 模糊帧上模型给不出转写，会回「图中无可见文字内容」这类元描述——视为无内容帧，
    否则会拿这句当正文生成一份垃圾纪要"""
    return len(text) < 6 or bool(re.search(r'无可见文字|没有文字|无文字|无法识别|无法辨认|不含文字|无任何文字', text))


class UnsupportedSource(Exception):
    """材料类型暂不支持自动生成"""


def _mmss(milliseconds: int) -> str:
    total = max(0, int(milliseconds)) // 1000
    return f'{total // 60:02d}:{total % 60:02d}'


def _touch(note_id: int, phase: str | None = None) -> None:
    """流水线心跳：刷新 updated_at（僵尸自愈按「无进展时长」判断），并可记录当前阶段。
    阶段随心跳落库后，卡死 / 重试耗尽时失败原因能直接带出停在哪一步，线上排查不再盲猜。"""
    updates = {'updated_at': timezone.now()}
    if phase:
        updates['last_phase'] = phase
    Note.objects.filter(pk=note_id).update(**updates)


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


class NoSpeechError(RuntimeError):
    """材料里没有可识别的人声（纯音乐 / 纯画面 / 静音）：区别于基础设施故障，提示文案面向用户。"""


def _asr_sentences(file_path: str, note: Note | None = None) -> list[tuple[str, str]]:
    """paraformer-v2 文件转写，返回 [(mm:ss, 句子文本)]"""
    try:
        size_mb = os.path.getsize(file_path) / 1024 / 1024
    except OSError:
        size_mb = -1
    if note is not None:
        _touch(note.pk, '上传转写服务')   # 大文件上传是最长的无心跳段，先打阶段点
    log.info('上传材料到转写服务 note=%s size=%.1fMB', note.pk if note else '?', size_mb)
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
              'parameters': {'language_hints': ['zh', 'en']}},
        timeout=60,
    )
    if res.status_code != 200:
        # 错误体里才有真实原因（欠费=Arrearage / 参数=InvalidParameter）；raise_for_status 只剩状态码
        try:
            err = res.json()
        except ValueError:
            err = {}
        code = str(err.get('code') or '')
        if code == 'Arrearage':
            raise RuntimeError('模型服务（百炼）账户欠费，请充值后点击重新处理')
        raise RuntimeError(
            f'转写任务提交失败（{res.status_code}）：{code or str(err.get("message") or "")[:120]}')
    task_id = res.json()['output']['task_id']
    for i in range(ASR_POLL_MAX):
        time.sleep(ASR_POLL_INTERVAL)
        if note is not None and i % 10 == 0:
            _touch(note.pk, '等待转写完成')   # 转写最长可轮询 30 分钟：期间心跳保活，防误判执行者已死
        out = requests.get(f'{ASR_BASE}/api/v1/tasks/{task_id}',
                           headers={'Authorization': f'Bearer {settings.DASHSCOPE_API_KEY}'},
                           timeout=30).json().get('output', {})
        status_ = out.get('task_status')
        if status_ == 'SUCCEEDED':
            break
        if status_ == 'FAILED':
            message = out.get('message') or 'ASR task failed'
            # 无有效语音片段（纯音乐/静音）与无音轨文件，任务都以 FAILED 落地：归入无人声，交给上层退化到画面理解
            if 'SUCCESS_WITH_NO_VALID_FRAGMENT' in message or 'no audio' in message.lower():
                raise NoSpeechError(NO_SPEECH_TEXT)
            raise RuntimeError(f'转写任务失败：{message}')
    else:
        raise TimeoutError('转写任务超时')
    result = out['results'][0]
    url = result.get('transcription_url') or (result.get('output') or {}).get('file_url')
    data = requests.get(url, timeout=60).json()
    sentences = (data.get('transcripts') or [{}])[0].get('sentences') or []
    return [(_mmss(s.get('begin_time', 0)), s.get('text', '').strip()) for s in sentences if s.get('text', '').strip()]


def _vision_payload(file_path: str) -> tuple[str, bytes]:
    """图片转 base64 前先压缩：qwen-vl 单图（含 base64）上限 10MB，
    手机拍摄的板书照片常超限；等比缩到长边 2400px、JPEG q88 后一般 <3MB，识别效果不受影响"""
    mime = mimetypes.guess_type(file_path)[0] or 'image/jpeg'
    with open(file_path, 'rb') as f:
        raw = f.read()
    if len(raw) <= 6 * 1024 * 1024 and mime in ('image/jpeg', 'image/png', 'image/webp'):
        return mime, raw
    try:
        from io import BytesIO
        from PIL import Image
        img = Image.open(file_path)
        img = img.convert('RGB') if img.mode not in ('RGB', 'L') else img
        img.thumbnail((2400, 2400))
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=88)
        data = buf.getvalue()
        if len(data) < len(raw):   # 压缩有效才采用
            return 'image/jpeg', data
    except Exception:
        pass  # 压缩失败退回原图，交由模型侧报错
    return mime, raw


def _vision_extract(file_path: str) -> str:
    """qwen-vl-plus 识别板书 / PPT 图片，转写出文字内容"""
    mime, raw = _vision_payload(file_path)
    b64 = base64.b64encode(raw).decode()
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


def _pdf_text(file_path: str) -> str:
    """PyMuPDF 按页抽取文本；扫描件（无文本层）明确报错提示改传图片"""
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # 旧版包名兼容
    pages = []
    with pymupdf.open(file_path) as doc:
        for page in doc:
            t = page.get_text().strip()
            if t:
                pages.append(t)
    text = '\n'.join(pages)
    if not text.strip():
        raise RuntimeError('PDF 中未提取到文本（可能是扫描件），请截图后作为图片材料上传')
    return text


def _docx_text(file_path: str) -> str:
    from docx import Document
    doc = Document(file_path)
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(' | '.join(cells))
    if not parts:
        raise RuntimeError('Word 文档中未提取到文本')
    return '\n'.join(parts)


def _pptx_text(file_path: str) -> str:
    from pptx import Presentation
    prs = Presentation(file_path)
    parts = []
    for no, slide in enumerate(prs.slides, 1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    texts.append(t)
            if getattr(shape, 'has_table', False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        texts.append(' | '.join(cells))
        if texts:
            parts.append(f'【第 {no} 页】\n' + '\n'.join(texts))
    if not parts:
        raise RuntimeError('PPT 中未提取到文本')
    return '\n'.join(parts)


def _xlsx_text(file_path: str) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(file_path, read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                rows.append(' | '.join(cells))
            if sum(len(r) for r in rows) > TEXT_MAX:
                break
        if rows:
            parts.append(f'【工作表 {ws.title}】\n' + '\n'.join(rows))
    wb.close()
    if not parts:
        raise RuntimeError('表格中未提取到内容')
    return '\n'.join(parts)[:TEXT_MAX]


def _csv_text(file_path: str) -> str:
    import csv as _csv
    rows = []
    with open(file_path, 'r', encoding='utf-8-sig', errors='ignore', newline='') as f:
        for row in _csv.reader(f):
            cells = [c.strip() for c in row if c.strip()]
            if cells:
                rows.append(' | '.join(cells))
            if sum(len(r) for r in rows) > TEXT_MAX:
                break
    if not rows:
        raise RuntimeError('CSV 中未提取到内容')
    return '\n'.join(rows)[:TEXT_MAX]


def _zip_member_name(info) -> str:
    """压缩包内文件名修复：Windows 资源管理器打 zip 不设 UTF-8 标志位，
    中文文件名在 zipfile 里默认按 cp437 解出乱码，需转回 GBK"""
    name = info.filename
    if not (info.flag_bits & 0x800):
        try:
            name = name.encode('cp437').decode('gbk', errors='ignore')
        except Exception:
            pass
    return name.replace('\\', '/')


def _read_zip(file_path: str) -> str:
    """压缩包抽取：逐个解出内嵌文档，按扩展名复用 _read_doc 解析后拼接。
    跳过目录 / 隐藏文件 / __MACOSX / 嵌套压缩包；单文件与总量设上限防压缩炸弹；
    单个内嵌文件解析失败时跳过（其余文件仍可生成），全部不可解析才报错。"""
    import tempfile
    import zipfile

    ZIP_DOC_EXTS = ('txt', 'md', 'markdown', 'pdf', 'docx', 'pptx', 'ppsx', 'xlsx', 'csv')
    parts: list[str] = []
    total = 0
    with zipfile.ZipFile(file_path) as zf:
        for info in zf.infolist():
            name = _zip_member_name(info)
            short = name.rsplit('/', 1)[-1]
            if info.is_dir() or name.startswith('.') or '__MACOSX' in name:
                continue
            ext = short.rsplit('.', 1)[-1].lower() if '.' in short else ''
            if ext == 'zip' or ext not in ZIP_DOC_EXTS:   # 嵌套压缩包不递归
                continue
            if info.file_size > ZIP_INNER_MAX:
                raise RuntimeError(f'压缩包内「{short}」超过 {ZIP_INNER_MAX // 1024 // 1024}MB，请拆分后再上传')
            total += info.file_size
            if total > ZIP_TOTAL_MAX:
                raise RuntimeError('压缩包解压后总内容超过 200MB，请拆分后再上传')
            data = zf.read(info)
            tmp_path = ''
            try:
                with tempfile.NamedTemporaryFile(suffix='.' + ext, delete=False) as tmp:
                    tmp.write(data)
                    tmp_path = tmp.name
                text = _read_doc(tmp_path)
            except Exception:
                continue   # 单文件解析失败不拖垮整包（如扫描件 PDF），临时文件由 finally 清理
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
            text = (text or '').strip()
            if text:
                parts.append(f'【文件：{short}】\n{text}')
    if not parts:
        raise RuntimeError('压缩包中没有可解析的文档（支持 txt / md / pdf / docx / pptx / xlsx / csv）')
    return '\n\n'.join(parts)[:TEXT_MAX]


def _read_doc(file_path: str) -> str:
    """讲义文档 / PPT / 压缩包抽取：按扩展名分派到对应解析器"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.zip':
        return _read_zip(file_path)
    if ext in ('.txt', '.md', '.markdown'):
        return _read_text(file_path)
    if ext == '.pdf':
        return _pdf_text(file_path)[:TEXT_MAX]
    if ext == '.docx':
        return _docx_text(file_path)[:TEXT_MAX]
    if ext in ('.pptx', '.ppsx'):
        return _pptx_text(file_path)[:TEXT_MAX]
    if ext == '.xlsx':
        return _xlsx_text(file_path)
    if ext == '.csv':
        return _csv_text(file_path)
    raise UnsupportedSource('该文档格式暂不支持自动生成纪要')


def _extract(note: Note) -> tuple[str, str]:
    """按材料类型抽取内容，返回 (内容文本, 来源标签)"""
    if not note.source:
        raise UnsupportedSource('未上传材料文件')
    with local_material_path(note.source) as path:
        return _extract_path(note, path)


def _video_frames_extract(video_path: str, note: Note) -> list[str]:
    """视频无人声时的画面理解：ffmpeg 抽帧 → qwen-vl 逐帧识别板书 / 幻灯片文字。
    抽帧两路合并：场景切换优先（板书 / 幻灯片翻页），不足上限按时长自适应间隔均匀补齐
    （长视频也能覆盖全片）；逐帧去重后识别。返回非空的识别文本（[画面 N] 前缀供引用溯源）。"""
    if note is not None:
        _touch(note.pk, '抽取关键帧')
    if shutil.which('ffmpeg') is None:
        raise UnsupportedSource('服务器缺少 ffmpeg，无法解析无语音视频的画面内容；请联系管理员安装')
    with tempfile.TemporaryDirectory(prefix='note-frames-') as tmp:
        # 注意：输出模板给 ffmpeg（%02d），收集时用通配（glob 不展开 printf 风格模板）。
        # 输出用 PNG：部分 ffmpeg 版本的 mjpeg 编码器会拒绝 limited-range YUV（Non full-range 报错）
        def run_ffmpeg(vf: str, stem: str) -> list[str]:
            subprocess.run(
                ['ffmpeg', '-y', '-loglevel', 'error', '-i', video_path,
                 '-vf', vf, '-frames:v', str(KEYFRAME_MAX), os.path.join(tmp, f'{stem}_%02d.png')],
                check=False, timeout=300, capture_output=True,
            )
            return sorted(glob.glob(os.path.join(tmp, f'{stem}_*.png')))

        # 幻灯片 / 板书翻页视频按场景切换抽帧最准；同版式深色幻灯片亮度差小会漏帧
        scene_frames = run_ffmpeg(f"select='gt(scene,{KEYFRAME_SCENE_THRESHOLD})'", 's')
        even_frames = []
        if len(scene_frames) < KEYFRAME_MAX:
            try:
                res = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', video_path],
                    capture_output=True, timeout=60,
                )
                duration = float(res.stdout.strip() or 0)
            except (OSError, subprocess.SubprocessError, ValueError):
                duration = 0.0
            if duration > 0:   # 均匀采样补齐：间隔随时长自适应，10 分钟视频 → 每 60s 一帧
                interval = max(2.0, duration / KEYFRAME_MAX)
                even_frames = run_ffmpeg(f'fps=1/{interval:g}', 'e')
        # 两路合并，同画面（如同一页幻灯片被重复采样）按内容去重，场景帧优先保留
        frames, seen = [], set()
        for frame in scene_frames + even_frames:
            digest = hashlib.md5(open(frame, 'rb').read()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            frames.append(frame)
            if len(frames) >= KEYFRAME_MAX:
                break
        if not frames:
            return []
        texts = []
        for i, frame in enumerate(frames, 1):
            if note is not None:
                _touch(note.pk, f'识别画面 {i}/{len(frames)}')
            try:
                text = _vision_extract(frame).strip()
            except Exception:
                # 单帧识别失败（模糊帧 / 纯色帧模型拒答）只跳过该帧，不能带崩整个视频
                log.warning('关键帧识别失败 note=%s frame=%s', note.pk if note else '?', frame)
                continue
            if text and not _is_empty_frame_text(text):
                texts.append(f'[画面 {i}] {text}')
        return texts


def _has_audio_stream(path: str) -> bool:
    """ffprobe 探测文件是否含音轨。无音轨的录屏 / 导出幻灯片视频直接走画面理解，
    省一次注定失败的转写上传（大文件上传在小带宽服务器上要几分钟）。"""
    if shutil.which('ffprobe') is None:
        return True   # 无法探测时不拦，交给转写服务侧报错
    try:
        res = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a',
             '-show_entries', 'stream=index', '-of', 'csv=p=0', path],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return bool(res.stdout.strip())


def _extract_path(note: Note, path: str) -> tuple[str, str]:
    kind = note.source_type or 'audio'
    if kind in ('audio', 'video'):
        sentences = []
        if kind == 'audio' or _has_audio_stream(path):
            try:
                sentences = _asr_sentences(path, note)
            except NoSpeechError:
                sentences = []   # 无人声：视频继续走画面理解，音频维持无人声失败文案
        if not sentences and kind == 'video':
            # 视频无人声 / 无音轨（纯演示 / 音乐 MV / 静音录屏）：退化为画面理解，从板书 / 幻灯片生成纪要
            frame_texts = _video_frames_extract(path, note)
            if frame_texts:
                return '\n\n'.join(frame_texts), '板书'
            raise NoSpeechError(NO_SPEECH_TEXT)   # 语音与画面都无可读内容
        if not sentences:
            raise NoSpeechError(NO_SPEECH_TEXT)
        return '\n'.join(f'[{ts}] {text}' for ts, text in sentences), '录音'
    if kind == 'image':
        return _vision_extract(path), '板书'
    if kind == 'doc':
        return _read_doc(path), '讲义'
    if kind == 'ppt':
        return _read_doc(path), 'PPT'
    raise UnsupportedSource('该格式暂不支持自动生成纪要，请上传 音频 / 视频 / 图片 / PPT / PDF / Word / 文本 / 压缩包')


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
        timeout=90,   # 长材料 JSON 输出可能超默认 30s，超时即整篇纪要静默失败
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


def _split_chunks(content: str, size: int = SUMMARIZE_CHUNK) -> list[str]:
    """按段落（行）边界把长文切成 ≤size 字符的片段；单行超长时兜底硬切"""
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for line in content.split('\n'):
        line = line.rstrip()
        if buf_len + len(line) + 1 > size and buf:
            chunks.append('\n'.join(buf))
            buf, buf_len = [], 0
        while len(line) > size:      # 单行超长（如压缩成一行的讲义）
            chunks.append(line[:size])
            line = line[size:]
        buf.append(line)
        buf_len += len(line) + 1
    if buf:
        chunks.append('\n'.join(buf))
    return [c for c in chunks if c.strip()]


def _summarize_part(content: str, source_label: str, idx: int, total: int) -> dict:
    """map 步骤：对单个片段抽取结构化要点（同一 JSON schema，不带跨段汇总职责）"""
    system = (
        '你是课堂纪要编辑。下面是长课堂材料的片段（共 %d 段，当前第 %d 段）。'
        '只根据该片段内容抽取结构化要点，只输出 JSON：'
        '{"theme": "一句话主题",'
        ' "points": [{"text": "核心知识点", "ts": "mm:ss"}],'
        ' "definitions": [{"term": "术语", "text": "解释", "ts": "mm:ss"}],'
        ' "emphasized": ["老师强调或布置的重点问题"]}'
        '要求：忠实于片段内容、不要编造；points 2~8 条、definitions 0~6 条、emphasized 0~4 条；'
        '行首带 [mm:ss] 时间戳时 ts 取真实时间点，否则省略 ts。' % (total, idx)
    )
    data = llm.chat_json(
        [{'role': 'system', 'content': system},
         {'role': 'user', 'content': f'课堂材料片段（来源：{source_label}）：\n{content}'}],
        temperature=0.2,
        timeout=90,
    )
    return data if isinstance(data, dict) else {}


def _merge_partials(partials: list[dict]) -> dict:
    """合并各片段结果：按内容去重、限量，作为 reduce 兜底也可直接使用"""
    def dedupe(items, cap):
        seen, out = set(), []
        for it in items:
            t = (it.get('text') if isinstance(it, dict) else str(it)).strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(it)
            if len(out) >= cap:
                break
        return out

    points, definitions, emphasized = [], [], []
    for p in partials:
        points.extend(x for x in (p.get('points') or []) if isinstance(x, dict) and x.get('text'))
        definitions.extend(x for x in (p.get('definitions') or []) if isinstance(x, dict) and x.get('term'))
        emphasized.extend(x for x in (p.get('emphasized') or []) if isinstance(x, str) and x.strip())
    return {
        'theme': next((str(p.get('theme') or '').strip() for p in partials if p.get('theme')), ''),
        'points': dedupe(points, 20),
        'definitions': dedupe(definitions, 12),
        'emphasized': dedupe(emphasized, 8),
    }


def _summarize_long(content: str, source_label: str, note_id: int | None = None) -> dict:
    """长文 map-reduce：分段抽取 → 合并去重 → 融合精简成最终 schema
    分段调用可能持续数分钟，逐段心跳保活，避免 worker 误判执行者已死而重复领取"""
    chunks = _split_chunks(content)
    partials = []
    for i, chunk in enumerate(chunks, 1):
        data = _summarize_part(chunk, source_label, i, len(chunks))
        if note_id is not None:
            _touch(note_id, f'生成分段 {i}/{len(chunks)}')
        if data:
            partials.append(data)
    merged = _merge_partials(partials)

    def render_points(items):
        return '\n'.join(f'- {x["text"]}' + (f' [{x["ts"]}]' if x.get('ts') else '') for x in items)

    candidate = (
        f'主题：{merged["theme"]}\n'
        f'知识点候选：\n{render_points(merged["points"]) or "（无）"}\n'
        f'术语候选：\n' + '\n'.join(f'- {x["term"]}：{x["text"]}' for x in merged['definitions'])
        + '\n重点问题候选：\n' + '\n'.join(f'- {x}' for x in merged['emphasized'])
    )
    system = (
        '你是课堂纪要主编。以下是对同一份课堂材料分段抽取后合并的候选要点，'
        '请融合成最终结构化纪要：合并重复与相近表述、按重要度排序，只输出 JSON：'
        '{"theme": "一句话课程主题",'
        ' "points": [{"text": "核心知识点", "ts": "mm:ss"}],'
        ' "definitions": [{"term": "术语", "text": "解释", "ts": "mm:ss"}],'
        ' "emphasized": ["老师强调或布置的重点问题"]}'
        '要求：忠实于候选要点、不编造；points 4~8 条、definitions 2~6 条、emphasized 1~4 条；'
        '候选中带 [mm:ss] 时保留该 ts，没有则省略。'
    )
    data = llm.chat_json(
        [{'role': 'system', 'content': system},
         {'role': 'user', 'content': candidate}],
        temperature=0.2,
        timeout=90,
    )
    if isinstance(data, dict) and data.get('points'):
        return data
    return merged   # reduce 失败时兜底用合并结果（已去重限量）


def _cancelled(note_id: int) -> bool:
    """停止生成：纪要已被 cancel 接口翻出处理态时，流水线在阶段边界放弃执行、不再落库。
    用户停止生成后若不取消，串行 worker 会把整轮旧任务跑完，新一轮生成只能干等（表现为卡住很久）。"""
    return not Note.objects.filter(pk=note_id, status=Note.Status.PROCESSING).exists()


def process_note(note_id: int) -> None:
    """流水线主体（由常驻 note_worker 进程领取执行）：抽取 → 生成（长文走分段 map-reduce）→ 入库；失败置为 failed"""
    close_old_connections()   # 常驻进程：清掉可能已失效的旧连接
    note = Note.objects.filter(pk=note_id).first()
    if note is None:
        return
    try:
        content, source_label = _extract(note)
        if _cancelled(note.pk):
            log.info('纪要已停止生成，流水线退出（抽取后）note=%s', note_id)
            return
        _touch(note.pk, '审核材料')   # 抽取（含最长转写轮询）结束，进入审核 / 生成阶段
        # 上传材料自动审核：命中高置信违规内容直接拒绝生成（课程材料含健康 / 政治等教学内容不算违规，
        # 只有词库高置信违规才拦；政治半命中交由 moderate 内部分类，中性教学材料放行）
        if moderate(f'{note.title}\n{content[:2000]}')['verdict'] == 'block':
            note.status = Note.Status.FAILED
            note.fail_reason = '材料中包含违规或不当内容，已被自动审核拦截；请检查材料后重新上传。'
            note.save(update_fields=['status', 'fail_reason', 'updated_at'])
            SensitiveAuditLog.objects.create(
                user=note.uploader, kind='blocked', action='block',
                question=mask_contact(f'{note.title}\n{content[:300]}')[:500],
                detail={'source': 'upload', 'note_id': note.pk},
            )
            log.info('纪要材料违规拦截 note=%s', note_id)
            return
        if _cancelled(note.pk):
            log.info('纪要已停止生成，流水线退出（审核后，跳过生成）note=%s', note_id)
            return
        _touch(note.pk, '生成纪要')
        fields = (_summarize(content, source_label) if len(content) <= SUMMARIZE_DIRECT_MAX
                  else _summarize_long(content, source_label, note_id=note.pk))
        if _cancelled(note.pk):
            # 生成完成但用户已停止：不保存结果（纪要保持失败态），不让停止的纪要以完成态复活
            log.info('纪要已停止生成，放弃保存结果 note=%s', note_id)
            return
        note.theme = fields['theme'] or note.title
        note.points = fields['points']
        note.definitions = fields['definitions']
        note.emphasized = fields['emphasized']
        note.transcript = content
        if source_label == '录音':
            stamps = re.findall(r'^\[(\d{1,2}:\d{2})\]', content, flags=re.M)
            note.duration = stamps[-1] if stamps else note.duration  # 末句时间 ≈ 时长
        note.status = Note.Status.DONE
        note.fail_reason = ''
        note.save()
        _touch(note.pk, '向量化入库')
        rag.index_note(note)  # 生成完成即向量化入库，供智能问答检索
    except NoSpeechError as exc:
        # 无可用语音属正常业务失败：原因直接面向用户展示，不打错误日志
        # 注意只能用 source.name（存储对象名）；source.path 在 OSS 存储上会抛 NotImplementedError，
        # 失败处理器自己崩掉会让纪要滞留处理态反复重试（线上 AIGC 视频卡死就是这个原因）
        log.info('纪要无有效语音 note=%s (%s)', note_id, note.source.name if note.source else '?')
        note.status = Note.Status.FAILED
        note.fail_reason = str(exc)
        note.save(update_fields=['status', 'fail_reason', 'updated_at'])
    except Exception as exc:
        log.exception('纪要流水线失败 note=%s', note_id)
        note.status = Note.Status.FAILED
        note.fail_reason = str(exc)[:200] or '材料处理异常，请重试或更换材料'
        note.save(update_fields=['status', 'fail_reason', 'updated_at'])
    finally:
        close_old_connections()   # 常驻进程：长阶段结束后释放连接，避免下次领取拿到失效连接


def start_processing(note: Note) -> None:
    """置为处理中并清零尝试计数，等待常驻 note_worker 领取执行（接口立即返回，前端轮询状态）。
    流水线不在 Web 进程内跑线程：gunicorn 重启 / 部署不再打断生成中的纪要。"""
    note.status = Note.Status.PROCESSING
    note.fail_reason = ''  # 重新处理时清掉上次的失败原因，避免处理中仍显示旧文案
    note.attempts = 0      # 手动重新处理 = 用户明确要求再试，重置自动重试预算
    note.last_phase = '排队等待'
    note.save(update_fields=['status', 'fail_reason', 'attempts', 'last_phase', 'updated_at'])


MAX_ATTEMPTS = 3                # 执行者崩溃遗留的处理态自动重试上限；耗尽后只等用户手动重新处理
CLAIM_STALE_SECONDS = 900       # 心跳停滞阈值：超过视为执行者已死，允许重新领取。
# 必须盖过最长的无心跳段——大文件（几十 MB 视频）在小带宽服务器上传转写服务可达 5 分钟以上，
# 阈值太小会把「慢」误判成「死」；自动重试只应发生在执行者真的消失时
STALE_PROCESSING_MINUTES = 30   # 读取路径兜底翻转阈值：重试次数已耗尽且久无心跳才翻失败


def stale_fail_reason(last_phase: str) -> str:
    """重试耗尽翻失败时的用户可读原因，带出最后停留的流水线阶段"""
    return (f'处理超时：后台多次尝试仍未完成（最后停留在「{last_phase or "未知"}」阶段），'
            '请点击重新处理再试；若反复出现请联系管理员。')


def claim_next_note() -> tuple[int, int] | None:
    """原子领取一份待处理纪要，返回 (id, 本次尝试序号)（无可领取时返回 None）。
    两类候选：新入库的（attempts=0，立即领取）；执行者死亡遗留的（attempts 未耗尽且心跳停滞，重新领取）。
    领取即 attempts+1，用条件更新保证并发 worker 不会重复执行同一份。"""
    now = timezone.now()
    stale_deadline = now - timedelta(seconds=CLAIM_STALE_SECONDS)
    candidate = (Note.objects.filter(status=Note.Status.PROCESSING)
                 .filter(Q(attempts=0) | Q(attempts__lt=MAX_ATTEMPTS, updated_at__lt=stale_deadline))
                 .order_by('attempts', 'updated_at')
                 .only('id', 'attempts')
                 .first())
    if candidate is None:
        return None
    claimed = Note.objects.filter(
        pk=candidate.pk, status=Note.Status.PROCESSING, attempts=candidate.attempts,
    ).update(attempts=F('attempts') + 1, updated_at=now, last_phase='正在处理')
    return (candidate.pk, candidate.attempts + 1) if claimed else None


def fail_stale_processing() -> int:
    """读取路径兜底：自动重试次数已耗尽且心跳久未更新的处理态纪要翻成失败（失败态才有「重新处理」入口）。
    次数未耗尽的由 note_worker 重新领取自动重试，不在读取路径翻失败——避免与自动重试竞争。"""
    deadline = timezone.now() - timedelta(minutes=STALE_PROCESSING_MINUTES)
    stale = Note.objects.filter(
        status=Note.Status.PROCESSING, attempts__gte=MAX_ATTEMPTS, updated_at__lt=deadline,
    ).only('id', 'last_phase')
    flipped = 0
    for note in stale:
        # 带出最后停留的阶段：同一阶段反复停滞，就说明该阶段在当前服务器环境有问题
        flipped += Note.objects.filter(pk=note.pk, status=Note.Status.PROCESSING).update(
            status=Note.Status.FAILED, fail_reason=stale_fail_reason(note.last_phase),
            updated_at=timezone.now())
    return flipped
