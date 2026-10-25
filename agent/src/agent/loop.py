"""Per-turn evidence, bounded LangChain middleware, and final-output validation."""
import json
import re
from typing import NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import before_model, after_model
from langchain_core.messages import SystemMessage
from langgraph.config import get_stream_writer

from .schemas import Answer, Chunk, Finding
from .services import CITATION, StudyService, _validated_answer, _short_questions

QA_TOOLS = {'retrieve_classroom', 'web_search', 'filter_evidence'}
TASK_TOOLS = {'answer_question', 'summarize_notes', 'generate_quiz', 'suggest_followups',
              'transcribe_image', 'get_current_user_profile', 'get_user_preferences',
              'save_user_preference', 'ingest_material'}


class CustomState(AgentState):
    model_call_count: NotRequired[int]
    turn_model_calls: NotRequired[int]
    evidence: NotRequired[list[dict]]
    selected_ids: NotRequired[list[str]]
    evidence_reviewed: NotRequired[bool]
    final_output: NotRequired[dict | None]
    turn_start: NotRequired[str]


def unavailable(reason='资料不足，暂时无法确认。请补充材料或换个角度提问。'):
    return {'answer': reason, 'sources': [], 'followups': [], 'mode': 'unavailable', 'degraded': True}


_DEGRADED_NOTE = '\n\n（注：以上内容未能完成全部来源校验，仅供参考。）'


def _clean_draft(text):
    """未校验草稿先去掉 URL 与「来源：」尾注——校验禁止外流的内容不随降级答案输出。"""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'^.*(来源|参考)[:：].*$', '', text, flags=re.MULTILINE)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _degraded(draft, followups=None, fallback=None):
    """校验未完成时保留已流式显示的草稿并附免责说明；草稿为空才回退报错文案。"""
    body = _clean_draft(draft or '')
    if not body or body.startswith('暂时无法确认'):
        # 模型按指示自弃（写了「暂时无法确认」）：套免责注反而观感怪异，给标准文案
        return fallback or unavailable()
    return {'answer': body + _DEGRADED_NOTE, 'sources': [], 'followups': followups or [],
            'mode': 'unavailable', 'degraded': True}


def _degraded_from_state(state, fallback=None):
    """取本轮最近一次（不带工具调用）的模型草稿用于降级输出。"""
    for message in reversed(state.get('messages') or []):
        if getattr(message, 'tool_calls', None):
            continue
        if message.type == 'ai':
            return _degraded(streamed_answer_prefix(message.content), fallback=fallback)
    return fallback or unavailable()


def task_result(state):
    """A model may select a task artifact, but cannot fabricate the artifact itself."""
    for message in reversed(state['messages']):
        if message.id == state.get('turn_start'):
            break
        if message.type == 'tool' and message.name in TASK_TOOLS:
            if getattr(message, 'status', 'success') != 'error':
                value = json.loads(message.content)
                if isinstance(value, dict):
                    return value
            break
    raise ValueError('本轮没有可返回的任务工具结果')


def validate_final(state, content):
    parsed = json.loads(content)
    if parsed == {'task_result': True}:
        return task_result(state)
    answer = Answer.model_validate(parsed)
    if len(answer.answer) > 32000:
        raise ValueError('回答过长')
    labels = set(CITATION.findall(answer.answer))
    selected = set(state.get('selected_ids', []))
    if labels and (not state.get('evidence_reviewed') or not labels <= selected):
        raise ValueError('只能引用本轮 filter_evidence 保留的来源编号')
    evidence = state.get('evidence', [])
    chunks = [Chunk.model_validate(item['value']) for item in evidence if item['kind'] == 'classroom']
    findings = [Finding.model_validate(item['value']) for item in evidence if item['kind'] == 'external']
    text = _validated_answer(answer.answer, chunks, findings)
    if text is None:
        raise ValueError('每项结论必须引用真实证据，不能编造 URL 或来源说明')
    if not labels:
        # Model's unsupported prose is never accepted as fact — but it was already
        # streamed, so keep it with a caveat instead of erasing it with an error.
        return _degraded(answer.answer, _short_questions(answer.followups, 3, 16, []))
    mode = 'fusion' if any(x.startswith('E') for x in labels) and any(not x.startswith('E') for x in labels) else 'external' if all(x.startswith('E') for x in labels) else 'kb'
    return StudyService._finish_answer(text, _short_questions(answer.followups, 3, 16, []), mode, chunks, findings)


def build_middleware(max_model_calls):
    @before_model(state_schema=CustomState, can_jump_to=['end'])
    def budget(state, runtime):
        count = state.get('turn_model_calls', 0)
        if count >= max_model_calls:
            return {'final_output': _degraded_from_state(state, unavailable(
                '暂时无法确认：已达到本次检索与修正轮次上限，请缩小问题范围后重试。')), 'jump_to': 'end'}
        # This status is public progress, not the model's hidden reasoning.
        get_stream_writer()({'event': 'status', 'data': {'stage': 'reasoning', 'round': count + 1}})
        return {'turn_model_calls': count + 1, 'model_call_count': state.get('model_call_count', 0) + 1}

    @after_model(state_schema=CustomState, can_jump_to=['model'])
    def validate(state, runtime):
        message = state['messages'][-1]
        if message.type != 'ai':
            return None
        if message.tool_calls:
            if len(message.tool_calls) <= 1:
                return None
            # Reject parallel tool batches before they run: evidence IDs must
            # be stable and mutation/search costs must obey the turn budget.
            from langchain_core.messages import ToolMessage
            return {'messages': [ToolMessage(content='一次只调用一个工具；请根据结果决定下一步。',
                tool_call_id=call['id'], name=call['name'], status='error') for call in message.tool_calls], 'jump_to': 'model'}
        try:
            if message.response_metadata.get('finish_reason') not in (None, 'stop'):
                raise ValueError('模型输出未正常结束')
            output = validate_final(state, message.content)
            return {'final_output': output}
        except (ValueError, TypeError, KeyError):
            return {'messages': [SystemMessage(content=(
                '最终回答未通过校验。请先检索并用 filter_evidence 保留有依据的来源，再修正回答；'
                '课堂纪要查不到就换关键词调用 web_search 检索外部资料，外部结论用 [E编号] 引用'
                '并在句尾注明「补充自外部知识」；只能引用本轮保留的编号，每项结论带引用，'
                '不输出 URL。输出 JSON {"answer":"正文","followups":[]}；'
                '课堂与外部都确实查不到依据才写暂时无法确认。'
                '其他任务须先执行工具并审阅结果，然后输出 {"task_result":true}。'))], 'jump_to': 'model'}

    return [budget, validate]


def streamed_answer_prefix(content):
    """Decode only a leading JSON answer field, never tool arguments/reasoning.

    Partial escapes and UTF-16 pairs are withheld until they are complete.
    """
    match = re.match(r'^\s*\{\s*"answer"\s*:\s*"', content)
    if not match:
        return ''
    start = match.end()
    index = start
    while index < len(content):
        char = content[index]
        if char == '"':
            break
        if char == '\\':
            if index + 1 >= len(content):
                break
            if content[index + 1] == 'u':
                if index + 6 > len(content):
                    break
                index += 6
                continue
            index += 2
        else:
            index += 1
    try:
        decoded = json.loads('"' + content[start:index] + '"')
        if decoded and 0xD800 <= ord(decoded[-1]) <= 0xDBFF:
            decoded = decoded[:-1]
        return decoded
    except ValueError:
        return ''
