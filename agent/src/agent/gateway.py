"""Injectable DashScope models and Tavily search. No requests during import."""

import json
import os
import base64
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

from .prompts import SEARCH_SYSTEM, VISION_SYSTEM
from .schemas import SearchResult, SearchSource


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default='', repr=False)
    base_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    search_url: str = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation'
    chat_model: str = 'qwen-plus'
    fast_model: str = 'qwen-flash'
    vision_model: str = 'qwen3-vl-plus'
    timeout: float = 30.0
    enable_search: bool = True
    native_base_url: str = 'https://dashscope.aliyuncs.com/api/v1'
    asr_model: str = 'paraformer-v2'
    embedding_model: str = 'text-embedding-v4'
    embedding_dimensions: int = 1024
    search_provider: str = 'tavily'
    tavily_api_key: str = field(default='', repr=False)

    def __post_init__(self):
        if self.search_provider not in ('tavily', 'dashscope'):
            raise ValueError('AGENT_SEARCH_PROVIDER 必须为 tavily 或 dashscope')

    @classmethod
    def from_env(cls, *, env_file=None):
        # Bundled checkout shares Django's root .env, independently of cwd.
        # Standalone checkout retains its local .env; wheels use process/explicit config.
        root = Path(__file__).resolve().parents[2]
        backend = root.parent
        bundled = (backend / 'mysite/manage.py').is_file() or (backend / 'manage.py').is_file()
        path = Path(env_file) if env_file is not None else (backend if bundled else root) / '.env'
        values = dotenv_values(path, interpolate=False) if (env_file is not None or (root / 'pyproject.toml').is_file()) and path.is_file() else {}
        values = {k: v for k, v in values.items() if v is not None}
        values.update(os.environ)  # Read without mutating the process environment.
        get = values.get
        return cls(
            api_key=get('DASHSCOPE_API_KEY', ''),
            base_url=get('DASHSCOPE_BASE_URL', cls.base_url),
            search_url=get('DASHSCOPE_SEARCH_URL', cls.search_url),
            chat_model=get('AGENT_CHAT_MODEL', cls.chat_model),
            fast_model=get('AGENT_FAST_MODEL', cls.fast_model),
            vision_model=get('AGENT_VISION_MODEL', cls.vision_model),
            timeout=float(get('AGENT_TIMEOUT', '30')),
            enable_search=get('AGENT_ENABLE_SEARCH', 'true').lower() in ('1', 'true', 'yes'),
            native_base_url=get('DASHSCOPE_NATIVE_BASE_URL', cls.native_base_url),
            asr_model=get('AGENT_ASR_MODEL', cls.asr_model),
            embedding_model=get('AGENT_EMBEDDING_MODEL', cls.embedding_model),
            embedding_dimensions=int(get('AGENT_EMBEDDING_DIMENSIONS', '1024')),
            search_provider=get('AGENT_SEARCH_PROVIDER', 'tavily').strip().lower(),
            tavily_api_key=get('TAVILY_API_KEY', ''),
        )


def web_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username and not any(c.isspace() for c in value)
    except ValueError:
        return False


class DashScopeGateway:
    def __init__(self, settings=None, client=None):
        self.settings = settings or Settings.from_env()
        self.client = client or httpx.Client(timeout=self.settings.timeout)
        self._owns_client = client is None

    def close(self):
        if self._owns_client:
            self.client.close()

    def _post(self, url, payload, *, api_key=None):
        key = self.settings.api_key if api_key is None else api_key
        if not key:
            return None
        try:
            response = self.client.post(url, json=payload, headers={'Authorization': f'Bearer {key}'}, timeout=self.settings.timeout)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    def _chat(self, messages, model, temperature, json_mode=False):
        payload = {'model': model, 'messages': messages, 'temperature': temperature}
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}
        data = self._post(self.settings.base_url.rstrip('/') + '/chat/completions', payload)
        try:
            choice = data['choices'][0]
            if choice.get('finish_reason') not in (None, 'stop'):
                return None
            text = choice['message']['content']
            return text if isinstance(text, str) and text.strip() else None
        except (TypeError, KeyError, IndexError):
            return None

    def chat_json(self, system, user, *, model='chat', temperature=.3):
        name = self.settings.fast_model if model == 'fast' else self.settings.chat_model
        text = self._chat([{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], name, temperature, True)
        if text is None:
            return None
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except ValueError:
            return None

    def stream_text(self, system, user, *, temperature=.3):
        """Yield actual provider deltas; truncated/error streams must not look complete."""
        if not self.settings.api_key:
            raise RuntimeError('未配置模型密钥')
        payload = {'model': self.settings.chat_model, 'temperature': temperature, 'stream': True,
                   'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
        stopped = False
        with self.client.stream('POST', self.settings.base_url.rstrip('/') + '/chat/completions',
                                json=payload, headers={'Authorization': f'Bearer {self.settings.api_key}'},
                                timeout=self.settings.timeout) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith('data:'):
                    continue
                value = line[5:].strip()
                if value == '[DONE]':
                    break
                data = json.loads(value)
                if not isinstance(data, dict) or data.get('error'):
                    raise RuntimeError('流式生成失败')
                choices = data.get('choices', [])
                if not choices:  # Usage-only frames.
                    continue
                choice = choices[0]
                text = choice.get('delta', {}).get('content')
                if isinstance(text, str) and text:
                    yield text
                reason = choice.get('finish_reason')
                if reason:
                    if reason != 'stop':
                        raise RuntimeError('流式正文未完整生成')
                    stopped = True
        if not stopped:
            raise RuntimeError('流式连接提前结束')

    def search(self, question):
        if not self.settings.enable_search:
            return None
        if self.settings.search_provider == 'dashscope':
            return self._search_dashscope(question)
        return self._search_tavily(question)

    def _search_tavily(self, question):
        data = self._post('https://api.tavily.com/search', {
            'query': question, 'topic': 'general', 'search_depth': 'basic',
            'max_results': 5, 'include_answer': False, 'include_raw_content': False,
        }, api_key=self.settings.tavily_api_key)
        candidates = data.get('results') if data else None
        if not isinstance(candidates, list):
            return None
        sources, excerpts, seen = [], [], set()
        for item in candidates:
            if not isinstance(item, dict):
                continue
            url, content = item.get('url'), item.get('content')
            if not web_url(url) or url in seen or not isinstance(content, str) or not content.strip():
                continue
            title = item.get('title')
            title = title.strip()[:300] if isinstance(title, str) and title.strip() else url
            seen.add(url)
            sources.append(SearchSource(title=title, url=url, site_name=urlsplit(url).hostname or ''))
            excerpts.append({'title': title, 'url': url, 'content': content.strip()[:4000]})
            if len(sources) >= 5:
                break
        # Keep each excerpt attached to the actual URL; the existing findings
        # validator only accepts URLs from this same source list.
        return SearchResult(summary=json.dumps(excerpts, ensure_ascii=False), sources=sources) if sources else None

    def _search_dashscope(self, question):
        data = self._post(self.settings.search_url, {
            'model': self.settings.chat_model,
            'input': {'messages': [{'role': 'system', 'content': SEARCH_SYSTEM}, {'role': 'user', 'content': question}]},
            'parameters': {'enable_search': True, 'result_format': 'message', 'search_options': {'forced_search': True, 'enable_source': True}},
        })
        try:
            output = data['output']
            summary = output['choices'][0]['message']['content']
            candidates = output['search_info']['search_results']
            if not isinstance(summary, str) or not isinstance(candidates, list):
                return None
            sources = []
            seen = set()
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                url = item.get('url')
                if not web_url(url) or url in seen:
                    continue
                seen.add(url)
                sources.append(SearchSource(title=str(item.get('title') or url), url=url, site_name=str(item.get('site_name') or '')))
            return SearchResult(summary=summary, sources=sources) if sources else None
        except (TypeError, KeyError, IndexError):
            return None

    def vision(self, image_url):
        # No local paths, arbitrary file reads or fetching URLs in this process.
        if not web_url(image_url) or not image_url.startswith('https://'):
            raise ValueError('图片需使用可供视觉模型访问的 HTTPS URL')
        return self._vision_content(image_url)

    def vision_bytes(self, image_bytes, mime='image/png'):
        if mime not in ('image/png', 'image/jpeg', 'image/webp') or not 0 < len(image_bytes) <= 10 * 1024 * 1024:
            raise ValueError('OCR 图片格式或大小无效')
        data_url = f'data:{mime};base64,' + base64.b64encode(image_bytes).decode('ascii')
        return self._vision_content(data_url)

    def _vision_content(self, image_url):
        return self._chat([{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': image_url}},
            {'type': 'text', 'text': VISION_SYSTEM},
        ]}], self.settings.vision_model, .1)
