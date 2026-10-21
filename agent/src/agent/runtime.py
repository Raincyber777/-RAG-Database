"""Construct and invoke the agent without import-time network/database effects."""

import hashlib
import json
import logging
from contextlib import contextmanager
from contextlib import closing
from uuid import uuid4

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from .backend import EmptyBackend
from .gateway import DashScopeGateway, Settings
from .prompts import AGENT_SYSTEM
from .schemas import UserContext
from .services import StudyService
from .tools import build_tools
from .loop import CustomState, build_middleware, streamed_answer_prefix, unavailable

logger = logging.getLogger(__name__)


def scoped_thread_id(context: UserContext, thread_id: str) -> str:
    if not thread_id.strip():
        raise ValueError('thread_id 不能为空')
    identity = json.dumps([context.tenant_id, context.user_id, context.note_id, thread_id], ensure_ascii=False)
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


class StudyAgent:
    """Invoke via this wrapper so checkpoint keys always include identity scope.

    The host authenticates UserContext and authorizes data in Backend. Do not
    accept context straight from an untrusted HTTP body. Close owned resources
    on shutdown, or use open_agent(). Serialize concurrent calls on one thread.
    """

    def __init__(self, graph, service, owns_gateway=False, max_model_calls=8):
        self._graph = graph
        self.service = service
        self._owns_gateway = owns_gateway
        self.max_model_calls = max_model_calls

    @staticmethod
    def _input(message):
        identity = uuid4().hex
        return {'messages': [{'role': 'user', 'content': message, 'id': identity}], 'turn_start': identity,
                'turn_model_calls': 0, 'evidence': [], 'selected_ids': [], 'evidence_reviewed': False, 'final_output': None}

    def close(self):
        if self._owns_gateway:
            self.service.gateway.close()

    def invoke(self, message: str, *, context: UserContext, thread_id: str) -> dict:
        if not isinstance(context, UserContext) or not message.strip():
            raise ValueError('需要有效的上下文和非空消息')
        config = {'configurable': {'thread_id': scoped_thread_id(context, thread_id)}, 'recursion_limit': self.max_model_calls * 6 + 6}
        try:
            state = self._graph.invoke(self._input(message), config=config, context=context)
        except Exception as exc:
            logger.warning('Agent run unavailable: %s', type(exc).__name__)
            return {'output': {'answer': '暂时无法确认：模型或会话服务暂不可用，请稍后重试。', 'degraded': True}, 'model_call_count': None}
        return self._result(state)

    @staticmethod
    def _result(state):
        return {'output': state.get('final_output') or unavailable(), 'model_call_count': state.get('model_call_count', 0)}

    def stream(self, message: str, *, context: UserContext, thread_id: str):
        """Stream tool events, then the same authoritative envelope as invoke()."""
        if not isinstance(context, UserContext) or not message.strip():
            raise ValueError('需要有效的上下文和非空消息')
        config = {'configurable': {'thread_id': scoped_thread_id(context, thread_id)}, 'recursion_limit': self.max_model_calls * 6 + 6}
        yield {'event': 'status', 'data': {'stage': 'routing'}}
        state = None
        buffer, displayed = '', ''
        try:
            with closing(self._graph.stream(self._input(message), config=config,
                                           context=context, stream_mode=['custom', 'values', 'messages'])) as events:
                for kind, data in events:
                    if kind == 'custom':
                        if data.get('event') == 'status' and data['data'].get('stage') == 'reasoning':
                            buffer = ''
                            if displayed:
                                yield {'event': 'replace', 'data': {'text': ''}}
                                displayed = ''
                        yield data
                    elif kind == 'values':
                        state = data
                    elif kind == 'messages':
                        chunk, metadata = data
                        if metadata.get('langgraph_node') != 'model' or not isinstance(chunk.content, str):
                            continue
                        buffer += chunk.content
                        prefix = streamed_answer_prefix(buffer)
                        if prefix.startswith(displayed) and len(prefix) > len(displayed):
                            yield {'event': 'delta', 'data': {'text': prefix[len(displayed):], 'provisional': True}}
                            displayed = prefix
            result = self._result(state)
        except Exception as exc:
            logger.warning('Agent stream unavailable: %s', type(exc).__name__)
            result = {'output': {'answer': '暂时无法确认：模型或会话服务暂不可用，请稍后重试。', 'degraded': True}, 'model_call_count': None}
        # Always supply an authoritative replacement, including tool/graph failures.
        output = result['output']
        yield {'event': 'replace', 'data': {'text': output.get('answer', ''), 'output': output, 'degraded': output.get('degraded', False)}}
        yield {'event': 'done', 'data': result}


def build_agent(backend=None, *, settings=None, gateway=None, model=None, checkpointer=None, store=None,
                ingestor=None, tool_names=None, system_prompt=None, max_model_calls=8):
    """Inject data and persistence; hosts may restrict the tool set for typed APIs.

    tool_names=None exposes all tools. Empty or unknown allowlists fail closed.
    system_prompt is trusted host configuration, never a request-body field.
    """
    settings = settings or Settings.from_env()
    if not isinstance(max_model_calls, int) or not 2 <= max_model_calls <= 20:
        raise ValueError('max_model_calls 必须为 2~20')
    if model is None:
        if not settings.api_key:
            raise ValueError('请配置 DASHSCOPE_API_KEY，或注入测试模型；规则降级可直接调用 StudyService')
        from langchain_openai import ChatOpenAI
        model = ChatOpenAI(model=settings.chat_model, api_key=settings.api_key,
                           base_url=settings.base_url, temperature=.3,
                           timeout=settings.timeout, max_retries=0,
                           model_kwargs={'parallel_tool_calls': False})
    owns_gateway = gateway is None
    gateway = gateway if gateway is not None else DashScopeGateway(settings)
    from .knowledge import KnowledgeBase
    if ingestor is None and isinstance(backend, KnowledgeBase):
        from .extraction import Extractor
        from .ingestion import Ingestor
        from .media import SpeechTranscriber
        ingestor = Ingestor(backend, Extractor(ocr=gateway.vision_bytes), SpeechTranscriber(gateway))
    service = StudyService(backend if backend is not None else EmptyBackend(), gateway, ingestor)
    try:
        tools = build_tools(service)
        if tool_names is not None:
            allowed = set(tool_names)
            if not allowed or allowed - {t.name for t in tools}:
                raise ValueError('工具白名单为空或包含未知工具')
            tools = [t for t in tools if t.name in allowed]
        graph = create_agent(model=model, tools=tools, system_prompt=system_prompt if system_prompt is not None else AGENT_SYSTEM,
                             context_schema=UserContext, state_schema=CustomState,
                             middleware=build_middleware(max_model_calls),
                             store=store if store is not None else InMemoryStore(),
                             checkpointer=checkpointer if checkpointer is not None else InMemorySaver())
    except Exception:
        if owns_gateway:
            gateway.close()
        raise
    return StudyAgent(graph, service, owns_gateway, max_model_calls)


@contextmanager
def open_agent(backend=None, *, postgres_uri=None, initialize_schema=False, **kwargs):
    """Optional Postgres checkpoints. setup() runs only when explicitly requested.

    Pass a persistent store separately if preferences must survive restarts.
    A caller-owned model, store or gateway is never closed here.
    """
    if postgres_uri:
        if 'checkpointer' in kwargs:
            raise ValueError('postgres_uri 与 checkpointer 只能选择一个')
        from langgraph.checkpoint.postgres import PostgresSaver
        with PostgresSaver.from_conn_string(postgres_uri) as checkpointer:
            if initialize_schema:
                checkpointer.setup()
            agent = build_agent(backend, checkpointer=checkpointer, **kwargs)
            try:
                yield agent
            finally:
                agent.close()
    else:
        agent = build_agent(backend, **kwargs)
        try:
            yield agent
        finally:
            agent.close()
