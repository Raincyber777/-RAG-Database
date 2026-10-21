"""Tool schemas expose task inputs; identities come only from ToolRuntime."""

import logging
from typing import Literal

from langchain.tools import ToolRuntime, tool
from langgraph.config import get_stream_writer

from .schemas import QuestionType, UserContext
from .extraction import ExtractionError

logger = logging.getLogger(__name__)
PreferenceName = Literal['language', 'explanation_style', 'difficulty', 'learning_goal']


def _safe(action):
    try:
        return action()
    except PermissionError:
        return {'error': '无权访问当前纪要，请在应用中选择已授权的纪要。', 'degraded': True}
    except ExtractionError as exc:
        return {'error': str(exc), 'degraded': True}
    except ValueError:
        return {'error': '输入参数不符合要求，请检查问题、题数、偏好或图片地址。', 'degraded': True}
    except Exception as exc:
        # Do not put provider payloads, SQL or credentials into tool messages.
        logger.warning('Agent tool unavailable: %s', type(exc).__name__)
        return {'error': '当前数据或模型服务不可用，请稍后重试。', 'degraded': True}


def build_tools(service):
    @tool
    def answer_question(question: str, runtime: ToolRuntime[UserContext]) -> dict:
        """回答当前课堂纪要的知识问题，自动检索、分档和校验引用；无依据明确说明。"""
        def action():
            item = runtime.store.get(namespace(runtime), 'preferences')
            return service.answer(question, runtime.context, preferences=dict(item.value) if item else {})
        return _safe(action)

    @tool
    def summarize_notes(runtime: ToolRuntime[UserContext]) -> dict:
        """把当前授权课堂材料整理成主题、知识点、术语和重点；长文自动分段融合。"""
        return _safe(lambda: service.summarize(runtime.context))

    @tool
    def generate_quiz(runtime: ToolRuntime[UserContext], count: int = 5, kinds: list[QuestionType] | None = None) -> dict:
        """依据当前纪要出 1~20 道练习题；可选 single/multiple/indefinite/judge/blank/answer。"""
        return _safe(lambda: service.quiz(runtime.context, count, kinds or []))

    @tool
    def suggest_followups(runtime: ToolRuntime[UserContext]) -> dict:
        """根据当前课程和本会话近期用户提问生成最多四条学习追问。"""
        history = [m.content for m in runtime.state.get('messages', []) if m.type == 'human' and isinstance(m.content, str)]
        return _safe(lambda: service.suggest(runtime.context, history[-6:]))

    @tool
    def transcribe_image(image_url: str, runtime: ToolRuntime[UserContext]) -> dict:
        """转写用户提供的板书/PPT HTTPS 图片链接；无法辨认的文字不猜测。"""
        return _safe(lambda: service.transcribe_image(image_url))

    @tool
    def get_current_user_profile(runtime: ToolRuntime[UserContext]) -> dict:
        """查询当前登录用户资料；身份由服务端上下文提供，不能查询其他用户。"""
        def action():
            profile = service.backend.profile(runtime.context)
            if profile is None:
                return {'profile': None, 'reason': '当前用户没有可用资料'}
            allowed = {'user_id', 'tenant_id', 'name', 'email', 'role'}
            return {'profile': {key: value for key, value in profile.items() if key in allowed}}
        return _safe(action)

    def namespace(runtime):
        return ('user_preferences', runtime.context.tenant_id, runtime.context.user_id)

    @tool
    def get_user_preferences(runtime: ToolRuntime[UserContext]) -> dict:
        """读取当前用户的学习偏好，按租户和用户隔离。"""
        def action():
            item = runtime.store.get(namespace(runtime), 'preferences')
            return {'preferences': dict(item.value) if item else {}}
        return _safe(action)

    @tool
    def save_user_preference(name: PreferenceName, value: str, runtime: ToolRuntime[UserContext]) -> dict:
        """仅在用户明确要求记住时保存语言、讲解方式、难度或学习目标；值为 1~200 字。"""
        def action():
            if not 1 <= len(value.strip()) <= 200:
                raise ValueError('偏好长度无效')
            item = runtime.store.get(namespace(runtime), 'preferences')
            preferences = dict(item.value) if item else {}
            preferences[name] = value.strip()
            runtime.store.put(namespace(runtime), 'preferences', preferences)
            return {'saved': True, 'preferences': preferences}
        return _safe(action)

    @tool
    def ingest_material(asset_id: str, runtime: ToolRuntime[UserContext]) -> dict:
        """导入宿主已登记的当前纪要材料：ASR/文本抽取、分块、向量化、入库。仅接受 asset_id，不接受文件路径。"""
        def action():
            writer = get_stream_writer()
            result = None
            for progress in service.ingestor.ingest(asset_id, runtime.context):
                writer({'event': 'status', 'data': progress})
                result = progress
            return result
        return _safe(action)

    result = [answer_question, summarize_notes, generate_quiz, suggest_followups,
              transcribe_image, get_current_user_profile, get_user_preferences, save_user_preference]
    if service.ingestor is not None:
        result.append(ingest_material)
    from .evidence_tools import build_evidence_tools
    return result + build_evidence_tools(service, _safe)
