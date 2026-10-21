"""DashScope embeddings and asynchronous file transcription adapters."""

import json
import math
import re
import time
from urllib.parse import urlsplit

from .gateway import web_url
from .materials import Segment, timestamp


def validate_vector(vector, dimensions):
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise ValueError('向量维度与索引不一致')
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in vector):
        raise ValueError('向量含无效数值')
    norm = math.hypot(*vector)
    if norm == 0 or not math.isfinite(norm):
        raise ValueError('拒绝零向量或溢出向量')
    return [float(v) for v in vector]


class Embedder:
    def __init__(self, gateway, model=None, dimensions=None):
        self.gateway = gateway
        self.model = model or gateway.settings.embedding_model
        self.dimensions = dimensions or gateway.settings.embedding_dimensions
        self.signature = f'{self.model}:{self.dimensions}'

    def embed(self, texts, batch_size=10):
        if not 1 <= batch_size <= 10 or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError('嵌入批次最多 10 条，文本不能为空')
        vectors = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            data = self.gateway._post(self.gateway.settings.base_url.rstrip('/') + '/embeddings', {
                'model': self.model, 'input': batch, 'dimensions': self.dimensions, 'encoding_format': 'float',
            })
            if not isinstance(data, dict) or not isinstance(data.get('data'), list):
                raise RuntimeError('向量化服务不可用，未写入材料')
            rows = data['data']
            if len(rows) != len(batch) or any(not isinstance(row, dict) or type(row.get('index')) is not int for row in rows):
                raise ValueError('向量结果数量或索引无效')
            by_index = {row['index']: row.get('embedding') for row in rows}
            if set(by_index) != set(range(len(batch))):
                raise ValueError('向量索引缺失或重复')
            vectors.extend(validate_vector(by_index[i], self.dimensions) for i in range(len(batch)))
        return vectors


class SpeechTranscriber:
    """URL-based paraformer jobs; yields status between bounded polling calls."""

    def __init__(self, gateway, poll_interval=2., timeout=300., max_polls=150):
        if poll_interval < 0 or timeout <= 0 or max_polls < 1:
            raise ValueError('ASR 轮询配置无效')
        self.gateway = gateway
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.max_polls = max_polls

    def _request(self, method, path, payload=None, *, asynchronous=False):
        settings = self.gateway.settings
        if not settings.api_key:
            raise RuntimeError('未配置 ASR 密钥')
        headers = {'Authorization': f'Bearer {settings.api_key}'}
        if asynchronous:
            headers['X-DashScope-Async'] = 'enable'
        response = self.gateway.client.request(method, settings.native_base_url.rstrip('/') + path,
                                               json=payload, headers=headers, timeout=settings.timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('output'), dict):
            raise RuntimeError('ASR 响应格式无效')
        return data['output']

    def _segments(self, result, source_name):
        url = result.get('transcription_url')
        # Download only provider-owned result locations; never forward API credentials.
        if not web_url(url) or not url.startswith('https://') or not urlsplit(url).hostname.endswith('.aliyuncs.com'):
            raise RuntimeError('ASR 返回了无效的结果地址')
        with self.gateway.client.stream('GET', url, timeout=self.gateway.settings.timeout, follow_redirects=False) as response:
            if response.status_code != 200:
                raise RuntimeError('无法读取 ASR 转写结果')
            raw = bytearray()
            for part in response.iter_bytes():
                raw.extend(part)
                if len(raw) > 20 * 1024 * 1024:
                    raise RuntimeError('ASR 结果超过大小限制')
        data = json.loads(raw)
        segments = []
        for transcript in data.get('transcripts', []):
            for row in transcript.get('sentences', []):
                begin = row.get('begin_time')
                text = row.get('text')
                if type(begin) is not int or begin < 0 or not isinstance(text, str) or not text.strip():
                    raise RuntimeError('ASR 句子缺少真实时间戳或文字')
                segments.append(Segment(text=text.strip(), ref=f'{source_name} 录音 {timestamp(begin)}', timestamp_ms=begin))
        if not segments:
            raise RuntimeError('ASR 未识别到可用文字')
        return sorted(segments, key=lambda s: s.timestamp_ms)

    def transcribe(self, audio_url, source_name='课堂录音'):
        if not web_url(audio_url) or not audio_url.startswith('https://'):
            raise ValueError('录音需提供百炼可读取的 HTTPS URL')
        deadline = time.monotonic() + self.timeout
        task = self._request('POST', '/services/audio/asr/transcription', {
            'model': self.gateway.settings.asr_model, 'input': {'file_urls': [audio_url]},
            'parameters': {'channel_id': [0], 'language_hints': ['zh', 'en']},
        }, asynchronous=True)
        task_id = task.get('task_id')
        if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', task_id):
            raise RuntimeError('ASR 未返回有效任务编号')
        for index in range(self.max_polls):
            if time.monotonic() >= deadline:
                break
            status = task.get('task_status')
            if status == 'SUCCEEDED':
                rows = task.get('results', [])
                if len(rows) != 1 or rows[0].get('subtask_status') != 'SUCCEEDED':
                    raise RuntimeError('录音转写未完整成功')
                yield {'stage': 'transcribed', 'segments': self._segments(rows[0], source_name)}
                return
            if status not in ('PENDING', 'RUNNING'):
                raise RuntimeError('录音转写任务失败')
            yield {'stage': 'transcribing', 'task_id': task_id, 'status': status}
            if index:
                time.sleep(min(self.poll_interval, max(0., deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
            task = self._request('GET', f'/tasks/{task_id}')
        raise TimeoutError('录音转写超时，材料尚未写入；可稍后重新导入')
