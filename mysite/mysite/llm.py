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
MODEL_VL = 'qwen3-vl-plus'         # 视觉：板书 / PPT 图片识别（账号未开通 qwen-vl-plus，用 qwen3-vl-plus）
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


SEARCH_URL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation'
SEARCH_TIMEOUT = 60   # 联网搜索比普通对话慢（服务端先检索再生成），放宽超时
CONF_LABEL = {'high': '高', 'mid': '中', 'low': '低'}


def search_answer(question: str, timeout: int = SEARCH_TIMEOUT) -> tuple[str, list[dict]] | None:
    """联网搜索问答（原生 API enable_search 插件：服务端先检索再生成）。
    返回 (基于搜索结果的回答文本, 真实搜索结果清单 [{title, url, site_name}])；失败返回 None。
    走原生接口而非 compatible-mode：只有原生接口回传 search_info.search_results 真实来源清单，
    供后续步骤做来源白名单校验，防止模型编造链接。
    """
    if not available():
        return None
    try:
        res = requests.post(
            SEARCH_URL,
            headers={'Authorization': f"Bearer {settings.DASHSCOPE_API_KEY}"},
            json={
                'model': MODEL_CHAT,
                'input': {'messages': [
                    {'role': 'system', 'content': '基于搜索结果用中文简洁、准确地回答。'},
                    {'role': 'user', 'content': question},
                ]},
                'parameters': {
                    'result_format': 'message',
                    'enable_search': True,
                    'search_options': {'enable_source': True, 'search_result': True, 'search_strategy': 'standard'},
                },
            },
            timeout=timeout,
        )
        res.raise_for_status()
        out = res.json().get('output', {})
        text = ((out.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
        results = [
            {'title': str(r.get('title') or ''), 'url': str(r.get('url') or ''),
             'site_name': str(r.get('site_name') or '')}
            for r in ((out.get('search_info') or {}).get('search_results') or [])
            if r.get('url')
        ]
        if not text.strip() or not results:
            return None
        return text, results
    except Exception:
        return None


def search_findings(question: str, timeout: int = SEARCH_TIMEOUT) -> list[dict] | None:
    """外部知识解析链路：联网搜索 → 模型清洗 / 去重 / 可信度判断 / 摘要提炼。
    返回 [{'point', 'source_title', 'source_url', 'confidence'}]（按可信度降序，最多 4 条）；
    source_url 只允许取自真实搜索结果清单（白名单校验，防编造），任一环节失败返回 None。
    """
    got = search_answer(question, timeout=timeout)
    if not got:
        return None
    text, results = got
    listing = '\n'.join(f"{i + 1}. {r['title']}（{r['site_name']}）{r['url']}"
                        for i, r in enumerate(results))
    data = chat_json(
        [
            {'role': 'system', 'content': (
                '你是外部资料审核员。依据给定的「搜索摘要」与「候选来源清单」，'
                '提炼回答学生问题最有用的外部知识要点：'
                '1）清洗：剔除与问题无关的广告、站点导航等噪声内容；'
                '2）去重：同一说法只保留一条最完整的表述；'
                '3）可信度：给每条标注 confidence（high=官方 / 权威媒体 / 文档站，mid=一般资讯站，low=论坛 / 自媒体）；'
                '4）每条的 source_url 必须原样取自候选来源清单，不得编造。'
                '只输出 JSON：{"findings": [{"point": "要点摘要", "source_title": "来源标题", '
                '"source_url": "链接", "confidence": "high|mid|low"}]}，最多 4 条。'
            )},
            {'role': 'user', 'content': f'学生问题：{question}\n\n搜索摘要：\n{text}\n\n候选来源清单：\n{listing}'},
        ],
        temperature=0.2,
        timeout=timeout,
    )
    if not isinstance(data, dict) or not isinstance(data.get('findings'), list):
        return None
    urls = {r['url'] for r in results}
    out: list[dict] = []
    for f in data['findings']:
        if not isinstance(f, dict):
            continue
        point = str(f.get('point') or '').strip()
        url = str(f.get('source_url') or '').strip()
        if not point or url not in urls:   # 白名单校验：不在真实清单里的来源直接丢弃
            continue
        hit = next((r for r in results if r['url'] == url), {})
        conf = str(f.get('confidence') or 'mid')
        out.append({
            'point': point,
            'source_title': str(f.get('source_title') or '').strip() or hit.get('title', ''),
            'source_url': url,
            'confidence': conf if conf in CONF_LABEL else 'mid',
        })
        if len(out) >= 4:
            break
    out.sort(key=lambda x: ('high', 'mid', 'low').index(x['confidence']))
    return out or None


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
