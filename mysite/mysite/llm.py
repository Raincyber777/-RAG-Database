"""
统一模型服务封装（阿里云百炼 OpenAI 兼容模式，https://dashscope.aliyuncs.com/compatible-mode/v1）
- key 未配置（settings.DASHSCOPE_API_KEY 为空）时 available() 为 False，
  所有调用直接返回 None，由调用方降级到规则版逻辑（demo 闭环不依赖外网）。
- 后续 RAG 问答 / 纪要生成 / 出题 / 追问建议共用本模块，换供应商只改这里。
"""
import json

import requests
from django.conf import settings

BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
MODEL_CHAT = 'qwen-plus'    # 主力：问答生成 / 纪要总结 / 出题（function calling / JSON 输出）
MODEL_LITE = 'qwen-flash'   # 轻量：追问建议等低质量要求场景（快、便宜；qwen-turbo 在当前账号限流不可用）
MODEL_EMBED = 'text-embedding-v4'  # 向量：RAG 检索（默认 1024 维）
MODEL_VL = 'qwen-vl-plus'          # 视觉：板书 / PPT 图片识别
EMBED_BATCH = 10            # 百炼 embeddings 单次最多 10 条输入
TIMEOUT = 30


def available() -> bool:
    return bool(getattr(settings, 'DASHSCOPE_API_KEY', ''))


def chat(messages: list[dict], model: str | None = None, json_mode: bool = False,
         temperature: float = 0.7, timeout: int = TIMEOUT) -> str | None:
    """对话补全，返回文本；未配置 key / 网络 / 接口异常一律返回 None（调用方自行降级）。"""
    if not available():
        return None
    payload: dict = {'model': model or MODEL_CHAT, 'messages': messages, 'temperature': temperature}
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    try:
        res = requests.post(
            f'{BASE_URL}/chat/completions',
            headers={'Authorization': f"Bearer {settings.DASHSCOPE_API_KEY}"},
            json=payload,
            timeout=timeout,
        )
        res.raise_for_status()
        return res.json()['choices'][0]['message']['content']
    except Exception:
        return None


def chat_json(messages: list[dict], **kwargs) -> dict | list | None:
    """JSON 模式对话，返回解析后的对象；解析失败同样返回 None。"""
    text = chat(messages, json_mode=True, **kwargs)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def chat_stream(messages: list[dict], model: str | None = None,
                temperature: float = 0.7, timeout: int = TIMEOUT):
    """流式对话补全（SSE）：返回逐段 yield 增量文本的生成器；
    key 未配置 / 连接失败返回 None，由调用方降级；流中途异常在迭代时抛出。
    """
    if not available():
        return None
    try:
        res = requests.post(
            f'{BASE_URL}/chat/completions',
            headers={'Authorization': f"Bearer {settings.DASHSCOPE_API_KEY}"},
            json={'model': model or MODEL_CHAT, 'messages': messages,
                  'temperature': temperature, 'stream': True},
            timeout=timeout,
            stream=True,
        )
        res.raise_for_status()
    except Exception:
        return None

    def gen():
        for raw in res.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith('data:'):
                continue
            payload = raw[5:].strip()
            if payload == '[DONE]':
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            text = ((chunk.get('choices') or [{}])[0].get('delta') or {}).get('content')
            if text:
                yield text

    return gen()


def embed(texts: list[str], timeout: int = TIMEOUT) -> list[list[float]] | None:
    """批量向量化（MODEL_EMBED，1024 维），返回与输入等长的向量列表；失败返回 None。"""
    if not available() or not texts:
        return None
    out: list[list[float]] = []
    try:
        for i in range(0, len(texts), EMBED_BATCH):
            res = requests.post(
                f'{BASE_URL}/embeddings',
                headers={'Authorization': f'Bearer {settings.DASHSCOPE_API_KEY}'},
                json={'model': MODEL_EMBED, 'input': texts[i:i + EMBED_BATCH]},
                timeout=timeout,
            )
            res.raise_for_status()
            data = res.json()['data']
            out.extend(item['embedding'] for item in sorted(data, key=lambda x: x.get('index', 0)))
        return out if len(out) == len(texts) else None
    except Exception:
        return None
