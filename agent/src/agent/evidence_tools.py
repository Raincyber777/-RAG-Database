"""Model-controlled retrieval and evidence selection; host-owned source identity."""
import json

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer
from langgraph.types import Command

from .schemas import UserContext


def build_evidence_tools(service, safe):
    def update(runtime, payload, **changes):
        return Command(update={**changes, 'messages': [ToolMessage(
            content=json.dumps(payload, ensure_ascii=False), tool_call_id=runtime.tool_call_id)]})

    def collect(runtime, entries):
        evidence = list(runtime.state.get('evidence', []))
        found = []
        for kind, value in entries:
            identity = ('text', 'ref') if kind == 'classroom' else ('point', 'source_url')
            existing = next((item for item in evidence if item['kind'] == kind and
                all(item['value'][key] == value[key] for key in identity)), None)
            if existing:
                found.append(existing)
                continue
            count = sum(item['kind'] == kind for item in evidence)
            if count >= 20:
                continue
            item = {'id': ('E' if kind == 'external' else '') + str(count + 1), 'kind': kind, 'value': value}
            evidence.append(item)
            found.append(item)
        return update(runtime, {'candidates': found, 'next': '检查相关性和冲突，可继续检索；用 filter_evidence 选择可引用证据。'},
                      evidence=evidence, selected_ids=[], evidence_reviewed=False)

    @tool
    def retrieve_classroom(query: str, runtime: ToolRuntime[UserContext]) -> dict:
        """检索当前授权课堂，返回带编号、相似度和出处的候选片段；可改写 query 多次检索。"""
        def action():
            if not query.strip() or len(query) > 1000:
                raise ValueError('检索词长度无效')
            service.backend.material(runtime.context)
            get_stream_writer()({'event': 'status', 'data': {'stage': 'retrieving'}})
            chunks = service.backend.retrieve(query, runtime.context, limit=4)
            return collect(runtime, [('classroom', c.model_dump()) for c in chunks if c.score >= .22][:4])
        return safe(action)

    @tool
    def web_search(query: str, runtime: ToolRuntime[UserContext]) -> dict:
        """联网补查课堂未覆盖的信息（默认 Tavily）；返回经来源白名单和低可信度过滤的候选要点，可换关键词继续搜索。"""
        def action():
            if not query.strip() or len(query) > 1000:
                raise ValueError('检索词长度无效')
            service.backend.material(runtime.context)  # Authorize before sending any query.
            get_stream_writer()({'event': 'status', 'data': {'stage': 'searching'}})
            return collect(runtime, [('external', f.model_dump()) for f in service._findings(query)])
        return safe(action)

    @tool
    def filter_evidence(keep_ids: list[str], runtime: ToolRuntime[UserContext]) -> dict:
        """审阅已有结果后选择与问题相关、可靠且不冲突的证据编号，剔除无关/重复项。只接受返回过的编号，可传空列表表示依据不足。"""
        def action():
            service.backend.material(runtime.context)
            evidence = runtime.state.get('evidence', [])
            known = {item['id'] for item in evidence}
            if not set(keep_ids) <= known:
                raise ValueError('不能选择未知证据')
            selected = list(dict.fromkeys(keep_ids))
            get_stream_writer()({'event': 'status', 'data': {'stage': 'reviewing', 'kept': len(selected)}})
            return update(runtime, {'selected': [item for item in evidence if item['id'] in selected],
                'next': '证据充分时生成最终 JSON；不足时继续检索。'}, selected_ids=selected, evidence_reviewed=True)
        return safe(action)

    return [retrieve_classroom, web_search, filter_evidence]
