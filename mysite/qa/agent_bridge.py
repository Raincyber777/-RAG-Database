"""Adapt the authenticated Django data layer to the installed Study Agent.

Imports are lazy so AGENT_ENABLED=False still works without the Agent package.
Each request owns its graph and HTTP client; tools recheck access in their worker.
"""
from contextlib import closing, contextmanager
from dataclasses import replace
from importlib.util import find_spec
import logging
from threading import get_ident
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections

from mysite import rag
from notes.models import Note

logger = logging.getLogger(__name__)
UNAVAILABLE = '暂时无法确认：课堂助教服务暂不可用，请稍后重试。'


def enabled():
    if not getattr(settings, 'AGENT_ENABLED', True):
        return False
    if find_spec('agent') is None:
        logger.warning('Agent package missing; using legacy Q&A. Install requirements-agent.txt.')
        return False
    return bool(agent_settings().api_key)


def agent_settings():
    """Keep shared .env search credentials while respecting explicit Django overrides."""
    from agent.gateway import Settings
    base = Settings.from_env()
    overrides = {}
    mapping = {'AGENT_BASE_URL': 'base_url', 'AGENT_CHAT_MODEL': 'chat_model',
               'AGENT_FAST_MODEL': 'fast_model', 'AGENT_TIMEOUT': 'timeout',
               'AGENT_ENABLE_SEARCH': 'enable_search', 'AGENT_SEARCH_PROVIDER': 'search_provider',
               'TAVILY_API_KEY': 'tavily_api_key'}
    for name, field in mapping.items():
        if hasattr(settings, name):
            overrides[field] = getattr(settings, name)
    if getattr(settings, 'DASHSCOPE_API_KEY', ''):
        overrides['api_key'] = settings.DASHSCOPE_API_KEY
    return replace(base, **overrides)


class DjangoNoteBackend:
    """Use existing Note/NoteChunk data, never a second Agent SQLite database."""

    def __init__(self, user, note):
        from agent.schemas import UserContext
        self.context = UserContext(str(user.pk), str(getattr(settings, 'AGENT_TENANT_ID', 'classroom')), str(note.pk) if note else '')
        self._request_thread = get_ident()

    @contextmanager
    def _authorized(self, context):
        if context != self.context:
            raise PermissionError('Context does not match authenticated request')
        worker = get_ident() != self._request_thread
        if worker:
            close_old_connections()
        try:
            user = get_user_model().objects.filter(pk=context.user_id, is_active=True, status='active').first()
            if user is None:
                raise PermissionError('Account unavailable')
            if not context.note_id:
                yield user, None
                return
            notes = Note.objects.filter(pk=context.note_id, status=Note.Status.DONE)
            if not user.is_admin_role:
                notes = notes.filter(uploader=user)
            note = notes.first()
            if note is None:
                raise PermissionError('Note unavailable')
            yield user, note
        finally:
            # Django request middleware cannot close ORM connections opened in
            # LangGraph's tool worker threads. Leave the request connection alone.
            if worker:
                connections.close_all()

    def material(self, context):
        from agent.schemas import Material
        with self._authorized(context) as (_, note):
            if note is None:
                return Material(title='通用问答', course='', text='')
            text = '\n'.join([note.theme, *[f"[{c['ref']}] {c['text']}" for c in rag.chunk_note(note)]])
            return Material(title=note.title, course=note.course, text=text)

    def retrieve(self, question, context, limit=4):
        from agent.schemas import Chunk
        with self._authorized(context) as (_, note):
            if note is None:
                return []
            return [Chunk(text=c.text, ref=c.ref or '课堂纪要', score=max(-1., min(1., float(score))))
                    for c, score in (rag.retrieve_scored(note, question, k=min(limit, 4)) or [])]

    def profile(self, context):
        with self._authorized(context) as (user, _):
            return {'user_id': str(user.pk), 'tenant_id': context.tenant_id,
                    'name': user.username, 'role': user.role}


@contextmanager
def _open(user, note):
    from agent.runtime import open_agent
    from agent.loop import QA_TOOLS
    from agent.prompts import AGENT_SYSTEM
    backend = DjangoNoteBackend(user, note)
    config = agent_settings()
    with open_agent(backend, settings=config, tool_names=QA_TOOLS,
                    system_prompt=AGENT_SYSTEM + '\n当前入口仅支持问答，使用已开放的检索/筛选工具循环，最后输出 answer/followups JSON。',
                    max_model_calls=getattr(settings, 'AGENT_MAX_MODEL_CALLS', 8)) as agent:
        yield agent, backend.context


def record_data(output):
    """Convert trusted Agent sources to the existing QaRecord/frontend schema."""
    answer = output.get('answer') or output.get('error') or UNAVAILABLE
    refs = []
    for source in output.get('sources') or []:
        external = source.get('kind') == 'external'
        seconds = source.get('timestamp_seconds')
        timestamp = f'{int(seconds) // 60:02d}:{int(seconds) % 60:02d}' if isinstance(seconds, (int, float)) and seconds >= 0 else ''
        ref = {'label': source.get('title', ''), 'time': timestamp,
               'detail': source.get('text') or source.get('title', ''),
               'kind': 'external' if external else 'kb', 'id': source.get('id', '')}
        if external:
            ref['url'] = source.get('url', '')
        refs.append(ref)
    return {'answer': answer, 'references': refs, 'followups': output.get('followups') or []}


def answer(user, note, question):
    try:
        with _open(user, note) as (agent, context):
            result = agent.invoke(question, context=context, thread_id=uuid4().hex)
        return record_data(result['output'])
    except Exception as exc:
        logger.warning('Agent Q&A unavailable: %s', type(exc).__name__)
        return record_data({})


def stream(user, note, question):
    final = None
    try:
        with _open(user, note) as (agent, context):
            with closing(agent.stream(question, context=context, thread_id=uuid4().hex)) as events:
                for event in events:
                    if event['event'] == 'done':
                        final = record_data(event['data']['output'])
                        break
                    if event['event'] in ('status', 'delta', 'replace'):
                        yield event
    except Exception as exc:
        logger.warning('Agent Q&A stream unavailable: %s', type(exc).__name__)
    final = final or record_data({})
    yield {'event': 'replace', 'data': {'text': final['answer']}}
    yield {'event': 'done', 'data': final}
