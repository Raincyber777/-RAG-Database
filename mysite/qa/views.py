"""
问答接口：
- 用户端：提问（一次性 / SSE 流式，返回答案 + 引用来源 + 追问）/ 历史记录 / 点赞 / 反馈埋点
- 安全门禁：敏感内容审核（拦截 / 政治引导）+ 健康档案提取与禁忌阻断（mysite/moderation.py）
- 数据进化：召回片段落库、反馈升降片段权重、知识库缺口聚合、训练样本池（强审核）
- 管理端：全部问答记录巡检 / 训练样本审核与导出
"""
import hashlib
import json
import re
from contextlib import closing

from django.db.models import F, Q
from django.db.models.functions import Greatest, Least
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mysite.utils import IsAdminRole
from mysite import llm, moderation, rag
from notes.models import Note, NoteChunk
from safety.masking import mask_contact, mask_health_detail
from safety.models import HealthProfile, SensitiveAuditLog

from .models import KnowledgeGap, QaFeedback, QaRecord, TrainingSample
from .serializers import (AskSerializer, KnowledgeGapSerializer, QaRecordSerializer,
                          TrainingSampleSerializer)
from . import agent_bridge
from .sessions import prepare_session, save_answer
from .images import prepare_images, image_events


def _score(question: str, text: str) -> int:
    """演示检索打分：问题与片段的 2-gram 重合数。TODO: 替换为向量相似度召回。"""
    q = ''.join(ch for ch in question if ch.isalnum())
    if len(q) < 2:
        return 0
    grams = {q[i:i + 2] for i in range(len(q) - 1)}
    return sum(1 for i in range(len(text) - 1) if text[i:i + 2] in grams)


def demo_answer(note: Note, question: str) -> tuple[str, list[dict]]:
    """
    演示版检索问答：按 2-gram 重合度取 Top3 片段拼装答案与引用来源。
    作为 RAG（mysite/rag.answer_note）失败时的降级路径，保证问答闭环不依赖外网。
    """
    chunks = rag.chunk_note(note)
    top = sorted(chunks, key=lambda c: _score(question, c['text']), reverse=True)[:3]
    top = [c for c in top if _score(question, c['text']) > 0] or chunks[:1]
    refs = [{'label': f'{note.title}', 'time': '', 'detail': c['ref']} for c in top]
    body = '\n'.join(f'· {c["text"]}' for c in top)
    answer = f'根据《{note.title}》的纪要内容：\n{body}\n\n以上内容可在「引用来源」中核对原文（演示检索）。'
    return answer, refs


# ---------- 安全门禁与进化辅助 ----------

def _audit(user, kind: str, action: str, question: str, detail: list | None = None):
    """敏感事件入审计日志：内容与词条一律先脱敏（避免明文留存）。"""
    try:
        SensitiveAuditLog.objects.create(
            user=user, kind=kind, action=action,
            question=mask_contact(question)[:500],
            detail=detail or [],
        )
    except Exception:
        pass  # 审计失败不影响问答主链路


# 寒暄/闲聊快判（零成本正则）：这类消息进 agent 只会因「每句必须带引用」的证据校验
# 反复打回，烧完 8 轮模型预算后以「轮次上限」替换掉已经流出的回答。
CHITCHAT_RE = re.compile(
    r'你好|您好|哈喽|哈罗|嗨|[Hh]i|hello|hey|在吗|在不在|早上好|中午好|下午好|晚上好|晚安'
    r'|你是谁|你叫什么|机器人|人工智能|你是ai|你是吗'
    r'|笑话|开玩笑|逗我|聊聊天|陪我聊|聊天吗|说句话'
    r'|谢谢|多谢|辛苦了|麻烦了|再见|拜拜|测试|test|ping'
)

# 「问助手自己/产品功能」的元问题：课程笔记里永远找不到可引用的证据，
# 进 agent 只会烧光轮次预算后报错，同样路由 legacy 通用问答。
META_RE = re.compile(
    r'上下文(?:长度|能力|窗口|限制)|几轮|多少轮|多轮对话'
    r'|外[部来](?:知识|资料)|联网|实时搜索|上网搜'
    r'|你的(?:名字|功能|能力|作者|开发者|模型|版本)'
    r'|(?:能|可以|支持|会)(?:帮我)?(?:上传|生成|识别|解析|导出|下载)'
    r'|(?:生成|制作)(?:学习)?纪要|纪要功能'
    r'|^如何上传|^怎么上传'
)

GREETING_PREFIX_RE = re.compile(
    r'^(?:你好|您好|哈喽|哈罗|嗨|[Hh]i|hello|hey)[，,。.~！!？?\s]+'
)


def is_chitchat(question: str) -> bool:
    """寒暄快判（仅 ≤16 字短句）：问候语带正题前缀时（如「你好，索引怎么建」）
    先剥前缀再判，避免把正经课程问题误当闲聊。"""
    text = (question or '').strip()
    if not text or len(text) > 16:
        return False
    stripped = GREETING_PREFIX_RE.sub('', text)
    if stripped != text:
        return not stripped.strip() or bool(CHITCHAT_RE.search(stripped))
    return bool(CHITCHAT_RE.search(text))


def is_meta_question(question: str) -> bool:
    text = (question or '').strip()
    return bool(text) and len(text) <= 16 and bool(META_RE.search(text))


# 「问助手自己/产品功能」的固定介绍文案：这类问题走外检会「查无依据」，直接给产品能力介绍
META_INTRO = (
    '当然可以！我是智学纪要的课堂助教，可以：\n'
    '1. 基于你上传的课堂纪要回答课程问题，回答会标注来源；\n'
    '2. 联网检索外部资料，补充课内没有的内容（外部结论会注明「补充自外部知识」）；\n'
    '3. 生成学习纪要、出练习题，还支持图片提问。\n'
    '把课堂材料上传到「课堂纪要」后体验最完整，有什么想问的尽管来～'
)


# 「我上传的东西」类问题（我上传了…/我的纪要…）：系统里查得到真实记录，
# 不能落进通用联网问答让模型凭空答「无法查看」。命中即用纪要数据作答。
UPLOAD_QA_RE = re.compile(r'我(?:上传|传了|导入了|上传过)|我的(?:纪要|笔记|文件|材料|音频|视频|课件)')


def is_upload_question(question: str) -> bool:
    """「我上传了…」类问题的路由级判定：命中即强制 legacy（用真实纪要记录作答）。"""
    return bool(UPLOAD_QA_RE.search((question or '').strip()))


def _answer_upload_question(user, question: str) -> tuple[str, list, list] | None:
    """用用户真实纪要回答「我上传了…」类问题；未命中返回 None 走原链路。"""
    if not UPLOAD_QA_RE.search(question or ''):
        return None
    total = Note.objects.filter(uploader=user).count()
    if not total:
        return ('目前还没有查到你的上传记录。把课堂材料上传到「课堂纪要」并生成纪要后，'
                '我就能基于它回答问题啦。'), [], []
    recent = list(Note.objects.filter(uploader=user).order_by('-created_at')[:5])
    lines = [f'查到啦！你在系统里目前共有 {total} 份纪要，最近上传的是：']
    for n in recent:
        state = '已完成' if n.status == Note.Status.DONE else (
            '生成中' if n.status == Note.Status.PROCESSING else '生成失败')
        lines.append(f'· 《{n.title}》（{n.course} · 录音 {n.duration or "时长未知"} · {state}）')
    lines.append('要针对某份纪要提问，在「智能问答」左侧选中它即可，我会引用其中的内容并标注录音时间点。')
    return '\n'.join(lines), [], []


def _rewrite_question(user, session_ref: str, question: str) -> tuple[str, bool]:
    """会话内追问改写：结合本会话最近两轮把「那第二种呢？」改写成独立完整的问题，
    检索与生成都用改写结果；同时判定是否为与学习无关的寒暄闲聊（供引擎路由，
    闲聊走 legacy 通用问答）。无历史 / 超时 / 失败一律回退 (原文, False)。"""
    if not session_ref:
        return '', False
    prior = list(QaRecord.objects.filter(user=user, session_ref=session_ref)
                 .order_by('-created_at').values('question', 'answer')[:2])
    if not prior:
        return '', False
    context = '\n'.join(f'问：{p["question"]}\n答（节选）：{p["answer"][:120]}' for p in reversed(prior))
    data = llm.chat_json(
        [{'role': 'system', 'content': (
            '根据最近的对话上下文，把学生这条新消息改写成一个不依赖上下文、独立完整的问题。'
            '消息本身已经完整时原样保留。同时判断这条消息是否「与课程学习无关」：'
            '寒暄闲聊（问候、玩笑、致谢告别、纯生活闲聊），或询问助手自身/产品功能而非课程'
            '内容（如能否引用外部资料、支持几轮对话、上下文多长、能否生成纪要、如何上传文档'
            '等），这两类都算 chitchat。只输出 JSON：'
            '{"question": "...", "chitchat": true|false}')},
         {'role': 'user', 'content': f'最近对话：\n{context}\n\n学生新消息：{question}'}],
        model=llm.MODEL_LITE, temperature=0.1, timeout=4,
    )
    if not isinstance(data, dict):
        return '', False
    rewritten = str(data.get('question') or '').strip()
    rewrite = rewritten[:500] if 2 <= len(rewritten) <= 300 else ''
    return rewrite, bool(data.get('chitchat'))


def _health_gate(user, question: str) -> tuple[bool, str]:
    """健康链路：词命中 → 提取建档；求建议且档案非空 → 禁忌比对，命中返回拦截文案。
    返回 (profile_updated, blocked_message)。"""
    profile_updated = False
    if not (moderation.health_hit(question) or moderation.health_advice_hit(question)):
        return False, ''
    profile, _ = HealthProfile.objects.get_or_create(user=user)
    if moderation.health_hit(question):
        extracted = moderation.extract_health(question)
        if extracted and profile.merge(extracted):
            profile.save()
            profile_updated = True
            _audit(user, 'health_extract', 'extract', question,
                   mask_health_detail([it for items in extracted.values() for it in items]))
    if moderation.health_advice_hit(question):
        items = profile.items()
        if items:
            conflicts = moderation.contraindication_conflict(question, items)
            if conflicts:
                _audit(user, 'health_block', 'block', question, mask_health_detail(conflicts))
                return profile_updated, moderation.health_block_message(conflicts)
    return profile_updated, ''


def _prepare_ask(user, data: dict) -> dict:
    """提问前置三步：内容审核 → 健康门禁 → 会话改写（含闲聊判定）。
    返回 {'reject': msg} / {'health_block': msg} / 正常 {'question', 'rewrite', 'chitchat', 'profile_updated'}。
    政治引导不在这里拦（作为正常回答流下发），由调用方按 moderate 结果处理。"""
    question = data['question']
    verdict = moderation.moderate(question)
    if verdict['verdict'] == 'block':
        _audit(user, 'blocked', 'block', question)
        return {'reject': verdict['message']}
    profile_updated, blocked = _health_gate(user, question)
    if blocked:
        return {'health_block': blocked, 'question': question, 'rewrite': '',
                'profile_updated': profile_updated, 'moderate': verdict}
    if data.get('images') or data.get('image_record_id'):
        rewrite, chitchat = '', False
    else:
        rewrite, chitchat = _rewrite_question(user, data.get('session_ref', ''), question)
    if not chitchat and is_chitchat(question):
        chitchat = True
    if not chitchat and is_meta_question(question):
        chitchat = True  # 元问题硬路由：flash 判定实测会漏（线上冒烟），正则为准
    return {
        'question': question,
        'moderate': verdict,
        'rewrite': rewrite,
        'profile_updated': profile_updated,
        'chitchat': chitchat,
    }


def _upsert_gap(record: QaRecord):
    """知识库缺口聚合：无依据问题按规范化 key 聚合计数，供管理端补充材料。"""
    key = ''.join(record.question.split())[:50].lower()
    if not key:
        return
    gap = KnowledgeGap.objects.filter(key=key).first()
    if gap:
        gap.count = F('count') + 1
        gap.sample_record = record
        gap.status = KnowledgeGap.Status.OPEN if gap.status != KnowledgeGap.Status.RESOLVED else gap.status
        gap.save(update_fields=['count', 'sample_record', 'updated_at'])
    else:
        KnowledgeGap.objects.create(key=key, question=record.question[:200], sample_record=record)


def _evolve_weights(record: QaRecord, action: str, value: str):
    """反馈闭环：按消息反馈升降召回 top1 片段的权重（clamp 0.2~2.0）。
    点赞 / 复制 / 停留 ≥30s 上调；点踩 / 重新提问下调；点踩且无知识库引用 → 记知识库缺口。"""
    delta = {'like': 0.05, 'copy': 0.05, 'dislike': -0.10, 'reask': -0.05}.get(action, 0.0)
    if action == 'dwell':
        try:
            delta = 0.05 if float(value or 0) >= 30 else 0.0
        except ValueError:
            delta = 0.0
    top = next((e for e in (record.retrieval or []) if e.get('chunk_id')), None)
    if delta and top:
        NoteChunk.objects.filter(id=top['chunk_id']).update(
            weight=Least(Greatest(F('weight') + delta, rag.WEIGHT_MIN), rag.WEIGHT_MAX))
    if action == 'dislike' and not any(r.get('kind') == 'kb' for r in (record.references or [])):
        _upsert_gap(record)


class SuggestionsView(APIView):
    """GET /api/qa/suggestions/ —— 欢迎页个性化追问建议
    依据近期提问涉及的纪要生成；配置了 DASHSCOPE_API_KEY 时由轻量 LLM 生成，
    未配置 / 调用失败时降级为规则版（模板拼装），保证演示闭环不依赖外网。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        cached = _cached_suggestions(user)
        if cached:
            return Response({'suggestions': cached})
        recent = list(QaRecord.objects.filter(user=user).select_related('note')[:6])
        # 通用问答记录（note 为空）不参与纪要向推荐，避免 None 崩溃
        note_records = [r for r in recent if r.note_id]
        if note_records:
            items = _llm_suggestions(note_records) or _rule_suggestions(note_records)
        elif recent:
            # 只有过自由问答（账号无已完成纪要）：通用知识向推荐，走联网通用问答
            items = list(GENERAL_SUGGESTIONS)
        else:
            # 还没提过问：优先从本人最新纪要出推荐题；无纪要则全通用
            latest = list(Note.objects.filter(uploader=user, status=Note.Status.DONE).order_by('-created_at')[:2])
            items = [f'《{n.title}》的重点是什么？' for n in latest] + DEFAULT_SUGGESTIONS[:2] if latest else list(GENERAL_SUGGESTIONS)
        items = items[:4]
        # 缓存到「当天 / 未提新问题」，避免每次进页面都调模型
        latest_pk = QaRecord.objects.filter(user=user).values_list('pk', flat=True).first()
        _SUGGESTION_CACHE[user.pk] = (latest_pk or 0, timezone.localdate().isoformat(), items)
        return Response({'suggestions': items})


# 建议缓存：{user_id: (最新问答 pk, 日期, items)} —— 提了新问题或跨天自动重新生成
_SUGGESTION_CACHE: dict[int, tuple[int, str, list[str]]] = {}


def _cached_suggestions(user) -> list[str] | None:
    latest_pk = QaRecord.objects.filter(user=user).values_list('pk', flat=True).first()
    hit = _SUGGESTION_CACHE.get(user.pk)
    if hit and hit[0] == (latest_pk or 0) and hit[1] == timezone.localdate().isoformat():
        return hit[2]
    return None


DEFAULT_SUGGESTIONS = [
    '帮我总结上一节课的重点',
    '泰勒展开有哪些易错点？',
    '进程和线程的区别是什么？',
    '二叉树的四种遍历怎么背？',
]

# 无纪要账号（未上传任何课堂资料）的通用推荐：不预设课堂内容，走联网通用问答
GENERAL_SUGGESTIONS = [
    '用通俗的话解释什么是机器学习',
    '帮我制定高数期末复习计划',
    '英语四级作文有哪些万能句型？',
    '什么是毕达哥拉斯定理？',
]


def _rule_suggestions(recent: list[QaRecord]) -> list[str]:
    """规则版个性化：最近提问的纪要 → 追问定义 / 重点问题 / 复习清单，不足 4 条用兜底补齐。"""
    out: list[str] = []
    notes: list[Note] = []
    for r in recent:
        if not any(n.id == r.note_id for n in notes):
            notes.append(r.note)

    last = recent[0]
    terms = [d.get('term') for d in (last.note.definitions or []) if d.get('term')]
    if terms:
        out.append(f'再考考我：《{last.note.title}》里的「{terms[0]}」')
    else:
        out.append(f'《{last.note.title}》这节课还有哪些重点没讲到？')

    if len(notes) >= 2:
        emphasized = notes[1].emphasized or []
        q = str(emphasized[0]).strip() if emphasized else ''
        for prefix in ('注意：', '注意:', '重点：', '重点:'):  # 剥掉常见前缀，避免「《标题》：注意：…」拗口
            if q.startswith(prefix):
                q = q[len(prefix):].strip()
        out.append(f'《{notes[1].title}》：{q}' if q else f'《{notes[1].title}》有哪些易错点？')
        out.append(f'把《{notes[0].course}》最近问过的内容串成一份复习清单')

    for s in DEFAULT_SUGGESTIONS:
        if len(out) >= 4:
            break
        if s not in out:
            out.append(s)
    return out[:4]


def _llm_suggestions(recent: list[QaRecord]) -> list[str] | None:
    """LLM 个性化追问：把近期提问 + 纪要上下文交给轻量模型，输出 4 条建议；失败返回 None。"""
    lines = [f'- 问题：{r.question}（纪要《{r.note.title}》/ {r.note.course}）' for r in recent[:6]]
    prompt = (
        '你是课堂学习助手。根据学生近期的提问记录，生成 4 条他接下来最可能想问的追问建议。'
        '要求：中文、口语化、每条不超过 18 字、不要与原问题重复、紧扣下面这些课程内容。\n'
        '近期提问：\n' + '\n'.join(lines) +
        '\n只输出 JSON：{"suggestions": ["...", "...", "...", "..."]}'
    )
    data = llm.chat_json([{'role': 'user', 'content': prompt}], model=llm.MODEL_LITE, temperature=0.8)
    if not isinstance(data, dict):
        return None
    items = data.get('suggestions')
    if not isinstance(items, list):
        return None
    clean = [str(x).strip() for x in items if isinstance(x, str) and 2 <= len(x.strip()) <= 30]
    return clean[:4] if len(clean) >= 2 else None


def _resolve_ask_note(request, note_id) -> Note | None:
    """提问目标纪要：显式指定时校验归属（本人或管理员），否则取本人最新已完成纪要"""
    if note_id is not None:
        notes = Note.objects.filter(pk=note_id)
        if not request.user.is_admin_role:
            notes = notes.filter(uploader=request.user)
        note = notes.first()
        if note is None:
            raise NotFound('纪要不存在或无权访问')
        if note.status != Note.Status.DONE:
            raise ValidationError({'detail': '请等待当前纪要处理完成后再提问'})
        return note
    return Note.objects.filter(uploader=request.user, status=Note.Status.DONE).first()


def _finish_answer(answer: str, question: str) -> str:
    """回答收尾：健康建议类提问统一追加医疗免责（代码拼装，不依赖模型）。"""
    if moderation.health_advice_hit(question):
        answer += moderation.MEDICAL_DISCLAIMER
    return answer


class AskView(APIView):
    """POST /api/qa/ask/ —— 提问，一次性返回完整回答（body: {note_id?, question}）"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = AskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        note = _resolve_ask_note(request, data.get('note_id'))
        session = prepare_session(request.user, data, note)
        if session.note_id:
            note = _resolve_ask_note(request, session.note_id)
        images = prepare_images(data, session, request.user)
        prep = _prepare_ask(request.user, data)
        if 'reject' in prep:
            return Response({'detail': prep['reject'], 'category': 'blocked'}, status=status.HTTP_400_BAD_REQUEST)
        question = prep['question']
        moderation_flag = ''
        use_agent = False
        collect: dict = {}

        if 'health_block' in prep:
            answer, refs, followups = prep['health_block'], [], []
            moderation_flag = 'health_block'
        elif prep['moderate'].get('verdict') == 'redirect':
            answer, refs, followups = prep['moderate']['message'], [], []
            moderation_flag = 'political'
            _audit(request.user, 'political', 'redirect', question)
        elif images:
            with closing(image_events(session, request.user, note, question, images,
                    profile_updated=prep['profile_updated'],
                    answer_suffix=moderation.MEDICAL_DISCLAIMER if moderation.health_advice_hit(question) else '')) as events:
                for event, payload in events:
                    if event == 'done':
                        response = Response(payload, status=status.HTTP_201_CREATED)
                        response['X-QA-Engine'] = 'vision'
                        return response
                    if event == 'error':
                        return Response(payload, status=status.HTTP_502_BAD_GATEWAY)
        else:
            # 自由对话（无纪要）与寒暄闲聊固定走 legacy 通用联网检索：agent 的强项在课堂材料问答，
            # 无纪要/无证据可引用时其检索与校验容易空转修正轮次（慢且可能落到轮次上限文案）。
            use_agent = (not images and note is not None and agent_bridge.enabled()
                         and not prep.get('chitchat') and not is_upload_question(question)
                         and (data.get('note_id') is not None or session.note_id))
            if use_agent:
                result = agent_bridge.answer(request.user, note, question)
                answer, refs, followups = result['answer'], result['references'], result['followups']
                tail = moderation.MEDICAL_DISCLAIMER
                if moderation.health_advice_hit(question) and '不构成医疗建议' not in answer:
                    answer += tail
            elif note is None or prep.get('chitchat') or is_upload_question(question):
                # 无纪要目标（账号还没有已完成纪要）或寒暄闲聊：通用模式，
                # 联网检索回答课堂之外的问题（闲聊没有可引用的证据，进 agent 必空转）
                upload_qa = _answer_upload_question(request.user, question)
                if is_meta_question(question):
                    answer, refs, followups = META_INTRO, [], []
                elif upload_qa is not None:
                    answer, refs, followups = upload_qa
                else:
                    result = rag.answer_general(prep['rewrite'] or question)
                    if result is not None:
                        answer, refs, followups = result
                    else:
                        answer, refs, followups = rag.no_general_answer(), [], []
                answer = _finish_answer(answer, question)
            else:
                # 真 RAG：向量召回 + LLM 生成（含追问）；key 未配置 / 检索生成失败时降级演示检索
                result = rag.answer_note(note, prep['rewrite'] or question, collect=collect)
                if result is not None:
                    answer, refs, followups = result
                else:
                    answer, refs = demo_answer(note, question)
                    followups = []
                    collect = {}
                answer = _finish_answer(answer, question)

        record = save_answer(session,
            user=request.user, note=note, question=question,
            answer=answer, references=refs, followups=followups,
            question_rewrite=prep['rewrite'],
            retrieval=collect.get('retrieval', []),
            used_external=collect.get('used_external', False),
            profile_updated=prep['profile_updated'],
            moderation=moderation_flag,
        )
        if collect.get('mode') == 'no_basis':
            _upsert_gap(record)
        response = Response(QaRecordSerializer(record).data, status=status.HTTP_201_CREATED)
        response['X-QA-Engine'] = 'agent' if use_agent else 'legacy'
        return response


def _stream_followups(note: Note | None, question: str, answer: str) -> list[str]:
    """流式回答完成后的追问建议：轻量模型依据问题与回答节选独立生成；失败返回空（通用问答无纪要上下文）"""
    head = (f'学生刚在《{note.title}》（{note.course}）提问：「{question}」'
            if note else f'学生刚提问：「{question}」')
    prompt = (
        f'{head}，助教已作答（节选）：{answer[:400]}'
        '\n请给 3 条学生最可能接着问的追问，中文、每条不超过 16 字。'
        '只提围绕课程知识、刚才回答内容或本产品功能（上传材料 / 生成纪要 / 出题练习）的问题；'
        '作业提交、成绩查询、截止时间等本产品没有的功能不要提。'
        '只输出 JSON：{"followups": ["...", "...", "..."]}'
    )
    data = llm.chat_json([{'role': 'user', 'content': prompt}], model=llm.MODEL_LITE, temperature=0.7)
    if not isinstance(data, dict) or not isinstance(data.get('followups'), list):
        return []
    return [str(x).strip() for x in data['followups']
            if isinstance(x, str) and 2 <= len(x.strip()) <= 20][:3]


def _sse(event: str, payload: dict) -> str:
    """SSE 帧：event + data 两行，空行结尾（ensure_ascii=False 保住中文可读性）"""
    return f'event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'


class AskStreamView(APIView):
    """POST /api/qa/ask/stream/ —— SSE 流式提问（text/event-stream）
    事件流：delta（回答增量）→ done（完整问答记录，含引用与追问）；
    安全拦截时下发 blocked（违规）或按正常回答流下发引导 / 安全警告（moderation 标记随 done 带回）。
    真流式：向量召回后 qwen-plus 逐 token 生成；RAG 不可用时降级演示检索一次性下发；
    流中断时以已生成内容收尾（无引用），保证前端总能拿到 done 记录入库。
    Agent 事件：status / delta / replace → done（已入库的完整问答记录）。
    replace 覆盖临时答案，只有最终答案入库；保留通用问答及 session_ref。
    AGENT_ENABLED=0 或缺少包/模型 key 时沿用旧 RAG 路径。
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = AskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        note = _resolve_ask_note(request, data.get('note_id'))
        session = prepare_session(request.user, data, note)
        if session.note_id:
            note = _resolve_ask_note(request, session.note_id)
        images = prepare_images(data, session, request.user)
        user = request.user
        # 引擎路由在 sse() 内 _prepare_ask 之后最终决定（改写步骤会判定寒暄闲聊）；
        # 下面先用零成本正则快判预置，供响应头在流开始前引用
        question_text = str(data.get('question') or '')
        # agent 只服务「显式绑定纪要」的会话：自由对话即便被 _resolve_ask_note 兜底到
        # 最新纪要，也不让 agent 接管——无目标纪要的常规问题会空转烧预算（线上实测）
        explicit_note = data.get('note_id') is not None or bool(session.note_id)
        # 流前响应头用预判：元问题/「我上传了…」也要算进 legacy，避免头标 agent 实跑 legacy
        general = (not explicit_note or is_chitchat(question_text)
                   or is_meta_question(question_text) or is_upload_question(question_text))
        use_agent = not images and not general and agent_bridge.enabled()

        def sse():
            nonlocal use_agent, general
            prep = _prepare_ask(user, data)
            if 'reject' in prep:
                yield _sse('blocked', {'category': 'blocked', 'message': prep['reject']})
                return
            question = prep['question']
            rag_question = prep['rewrite'] or question
            general = note is None or bool(prep.get('chitchat')) or is_upload_question(question)
            use_agent = (not images and explicit_note and not general
                         and agent_bridge.enabled())
            buf, refs = '', []
            extra: dict = {}
            moderation_flag = ''

            if 'health_block' in prep:
                buf = prep['health_block']
                moderation_flag = 'health_block'
                yield _sse('delta', {'text': buf})
            elif prep['moderate'].get('verdict') == 'redirect':
                buf = prep['moderate']['message']
                moderation_flag = 'political'
                _audit(user, 'political', 'redirect', question)
                yield _sse('delta', {'text': buf})
            elif images:
                with closing(image_events(session, user, note, question, images,
                        profile_updated=prep['profile_updated'],
                        answer_suffix=moderation.MEDICAL_DISCLAIMER if moderation.health_advice_hit(question) else '')) as events:
                    for event, payload in events:
                        yield _sse(event, payload)
                return
            elif use_agent:
                with closing(agent_bridge.stream(user, note, question)) as events:
                    for event in events:
                        if event['event'] == 'delta':
                            buf += event['data'].get('text', '')
                        elif event['event'] == 'replace':
                            # 桥接层已吞掉修正轮的中间 replace：这里只会在结束时收到权威全文
                            buf = event['data'].get('text', '')
                        elif event['event'] == 'done':
                            extra = dict(event['data'])
                            break
                        yield _sse(event['event'], event['data'])
                buf = extra.get('answer') or buf or agent_bridge.UNAVAILABLE
                refs = extra.get('references') or []
                tail = moderation.MEDICAL_DISCLAIMER
                if moderation.health_advice_hit(question) and '不构成医疗建议' not in buf:
                    buf += tail
                    yield _sse('delta', {'text': tail})
            else:
                if general and is_meta_question(question):
                    # 元问题（问助手自身/产品功能）：固定介绍文案，即时返回不走外检
                    buf = META_INTRO
                    yield _sse('delta', {'text': buf})
                else:
                    upload_qa = _answer_upload_question(user, question) if general else None
                    if upload_qa is not None:
                        buf = upload_qa[0]
                        yield _sse('delta', {'text': buf})
                    else:
                        try:
                            gen = rag.answer_stream_general(rag_question) if general else rag.answer_stream(note, rag_question)
                            for ev in gen:
                                if ev['type'] == 'delta':
                                    buf += ev['text']
                                    yield _sse('delta', {'text': ev['text']})
                                elif ev['type'] == 'end':
                                    refs = ev.get('refs') or []
                                    extra = {k: ev[k] for k in ('retrieval', 'used_external', 'mode') if k in ev}
                                    break
                                else:  # error：已有增量则就地收尾，否则走演示降级
                                    break
                        except Exception:
                            pass  # 流中断：以已生成内容收尾
                if not buf:
                    if general:
                        buf = rag.no_general_answer()
                        yield _sse('delta', {'text': buf})
                    else:
                        buf, refs = demo_answer(note, question)
                        yield _sse('delta', {'text': buf})
                tail = moderation.MEDICAL_DISCLAIMER
                if moderation.health_advice_hit(question) and '不构成医疗建议' not in buf:
                    buf += tail
                    yield _sse('delta', {'text': tail})

            followups = [] if moderation_flag else (
                list(extra.get('followups') or []) or _stream_followups(note, question, buf))
            record = save_answer(session,
                user=user, note=note, question=question,
                answer=buf, references=refs, followups=followups,
                question_rewrite=prep['rewrite'],
                retrieval=extra.get('retrieval', []),
                used_external=extra.get('used_external', False),
                profile_updated=prep['profile_updated'],
                moderation=moderation_flag,
            )
            if extra.get('mode') == 'no_basis':
                _upsert_gap(record)
            yield _sse('done', QaRecordSerializer(record).data)

        resp = StreamingHttpResponse(sse(), content_type='text/event-stream')
        resp['Cache-Control'] = 'no-cache'
        resp['X-Accel-Buffering'] = 'no'
        resp['X-QA-Engine'] = 'vision' if images else ('agent' if use_agent else 'legacy')
        return resp


class QaListView(ListAPIView):
    """GET /api/qa/ —— 我的问答历史（?search=&note=，note 为纪要 id 时只取该课的追问）"""

    serializer_class = QaRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # 两条过滤分支统一倒序（新→旧，-id 兜底同秒稳定序）：前端恢复会话时统一
        # reverse 成正序上屏（旧→新）。此前 session 分支是升序、note 分支是倒序，
        # 前端一视同仁 reverse 后自由对话恢复就成了新消息在上（2026-10-26 用户反馈）
        qs = QaRecord.objects.filter(user=self.request.user).select_related('note').order_by('-created_at', '-id')
        note_id = self.request.query_params.get('note')
        if note_id:
            qs = qs.filter(note_id=note_id)
        # 按前端自由对话会话拉历史（刷新后恢复会话消息）
        session = (self.request.query_params.get('session') or '').strip()
        if session:
            qs = qs.filter(session_ref=session)
        keyword = (self.request.query_params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(question__icontains=keyword)
        return qs


class QaLikeView(APIView):
    """POST /api/qa/{id}/like/ —— 点赞 / 取消点赞"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        record = QaRecord.objects.filter(pk=pk, user=request.user).first()
        if record is None:
            return Response({'detail': '记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        record.liked = not record.liked
        record.save(update_fields=['liked'])
        return Response(QaRecordSerializer(record).data)


class QaRollbackView(APIView):
    """POST /api/qa/rollback/ —— 会话回滚（Claude 式 rewind）：删除本会话中
    锚点问答及其后的全部记录，前端本地同步截断并把原问题填回输入框。
    record_id 取锚点回答的记录 id；id 单调递增，id__gte 即覆盖其后的追问。"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        session_ref = str(request.data.get('session_ref') or '').strip()
        try:
            record_id = int(request.data.get('record_id'))
        except (TypeError, ValueError):
            return Response({'detail': '无效的 record_id'}, status=status.HTTP_400_BAD_REQUEST)
        if not session_ref:
            return Response({'detail': '缺少 session_ref'}, status=status.HTTP_400_BAD_REQUEST)
        # 只删本人会话内的记录；反馈流水随外键级联清除
        qs = QaRecord.objects.filter(user=request.user, session_ref=session_ref, id__gte=record_id)
        deleted = qs.count()
        qs.delete()
        return Response({'deleted': deleted})


class QaFeedbackView(APIView):
    """POST /api/qa/{id}/feedback/ —— 按消息反馈埋点（like/dislike/copy/reask/dwell）
    写 QaFeedback 流水；like/dislike 同步 QaRecord.liked；并驱动片段权重升降（数据进化）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        record = QaRecord.objects.filter(pk=pk, user=request.user).first()
        if record is None:
            return Response({'detail': '记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        action = str(request.data.get('action') or '')
        if action not in QaFeedback.Action.values:
            return Response({'detail': '无效的反馈类型'}, status=status.HTTP_400_BAD_REQUEST)
        value = str(request.data.get('value') or '')[:200]
        QaFeedback.objects.create(user=request.user, record=record, action=action, value=value)
        if action in ('like', 'dislike'):
            record.liked = action == 'like'
            record.save(update_fields=['liked'])
        _evolve_weights(record, action, value)
        return Response({'action': action, 'liked': record.liked})


# ---------- 训练样本（强审核：生成 → 待审核 → 人工通过 → 导出） ----------

class TrainingGenerateView(APIView):
    """POST /api/admin/training/generate/ —— 从高质量问答生成训练样本候选
    选材：课内问答 + 有正反馈（点赞 / 复制 / 停留）+ 无审核标记 + 有召回片段 + 答案非兜底话术；
    清洗：剥来源说明尾缀（引用已在 references 结构化）、按 规范化问题+top片段 去重；
    过滤：qwen-plus 判定答案是否扎根召回片段；全部落 pending，人工审核后才可导出。
    单次最多处理 30 条，防止同步请求超时。"""

    permission_classes = [IsAuthenticated, IsAdminRole]
    LIMIT = 30

    def post(self, request):
        candidates = (QaRecord.objects
                      .filter(note__isnull=False, moderation='')
                      .filter(Q(liked=True) | Q(feedbacks__action__in=('copy', 'dwell')))
                      .exclude(answer__contains='暂时无法确认')
                      .distinct().order_by('-created_at'))
        created = skipped = judged = 0
        for record in candidates:
            if created + judged >= self.LIMIT:
                break
            if not record.retrieval or len(record.answer) < 50 or len(record.answer) > 2000:
                continue
            if TrainingSample.objects.filter(record=record).exists():
                skipped += 1
                continue
            # 清洗：剥掉回答尾部的来源说明（来源已结构化在 references）
            answer = record.answer.split('—— 来源说明 ——')[0].strip()
            if len(answer) < 50:
                continue
            top_id = next((e['chunk_id'] for e in record.retrieval if e.get('chunk_id')), None)
            if top_id is None:
                continue
            dedup_key = ''.join(record.question.split())[:80] + f'|{top_id}'
            dedup_hash = hashlib.sha256(dedup_key.encode()).hexdigest()
            if TrainingSample.objects.filter(dedup_hash=dedup_hash).exists():
                skipped += 1
                continue
            chunks = NoteChunk.objects.in_bulk([e['chunk_id'] for e in record.retrieval if e.get('chunk_id')])
            context = [{'text': chunks[e['chunk_id']].text, 'ref': e.get('ref', ''), 'score': e.get('score', 0)}
                       for e in record.retrieval if e.get('chunk_id') in chunks][:4]
            if not context:
                continue
            if llm.available() and not _sample_quality_ok(record.question, answer, context):
                skipped += 1
                judged += 1
                continue
            TrainingSample.objects.create(
                record=record, note=record.note,
                query=record.question, question_rewrite=record.question_rewrite,
                context=context, answer=answer, sources=record.references or [],
                dedup_hash=dedup_hash,
            )
            created += 1
        return Response({'created': created, 'skipped': skipped})


def _sample_quality_ok(question: str, answer: str, context: list[dict]) -> bool:
    """强模型预筛：答案是否扎根召回片段、完整且无明显编造；判定失败按通过处理（人工审核兜底）。"""
    ctx = '\n'.join(f'[{i + 1}] ({c["ref"]}) {c["text"]}' for i, c in enumerate(context))
    data = llm.chat_json(
        [{'role': 'system', 'content': (
            '你是训练样本质检员。给定学生问题、课堂纪要片段和候选答案，判断该问答是否适合作为训练样本：'
            '1）答案确实依据片段作答、无明显编造；2）回答完整、可直接用于教学场景；'
            '3）没有针对个人的内容。任一不满足 → ok=false。'
            '只输出 JSON：{"ok": true 或 false}')},
         {'role': 'user', 'content': f'问题：{question}\n\n纪要片段：\n{ctx}\n\n候选答案：\n{answer[:1200]}'}],
        model=llm.MODEL_CHAT, temperature=0.1, timeout=15,
    )
    if not isinstance(data, dict):
        return True
    return bool(data.get('ok', True))


class TrainingSampleListView(ListAPIView):
    """GET /api/admin/training/samples/?status= —— 训练样本池巡检"""

    serializer_class = TrainingSampleSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = TrainingSample.objects.select_related('note', 'record')
        st = (self.request.query_params.get('status') or '').strip()
        if st:
            qs = qs.filter(status=st)
        return qs


class TrainingSampleReviewView(APIView):
    """POST /api/admin/training/samples/{id}/review/ —— 人工审核（body: {action: approve|reject, note?}）"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk):
        sample = TrainingSample.objects.filter(pk=pk).first()
        if sample is None:
            return Response({'detail': '样本不存在'}, status=status.HTTP_404_NOT_FOUND)
        action = str(request.data.get('action') or '')
        if action == 'approve':
            sample.status = TrainingSample.Status.APPROVED
        elif action == 'reject':
            sample.status = TrainingSample.Status.REJECTED
        else:
            return Response({'detail': 'action 须为 approve / reject'}, status=status.HTTP_400_BAD_REQUEST)
        sample.reviewer_note = str(request.data.get('note') or '')[:200]
        sample.save(update_fields=['status', 'reviewer_note', 'updated_at'])
        return Response(TrainingSampleSerializer(sample).data)


class TrainingSampleExportView(APIView):
    """GET /api/admin/training/samples/export/ —— 导出已通过样本为 JSONL
    每行：{query, question_rewrite, context: [{text, ref, score}], answer, sources}"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        lines = [
            json.dumps({
                'query': s.query, 'question_rewrite': s.question_rewrite,
                'context': s.context, 'answer': s.answer, 'sources': s.sources,
            }, ensure_ascii=False)
            for s in TrainingSample.objects.filter(status=TrainingSample.Status.APPROVED)
                                            .select_related('note').order_by('id')
        ]
        resp = HttpResponse('\n'.join(lines) + ('\n' if lines else ''),
                            content_type='application/x-ndjson')
        resp['Content-Disposition'] = 'attachment; filename=training_samples.jsonl'
        return resp


class AdminQaListView(ListAPIView):
    """GET /api/admin/qa/ —— 全部问答记录（?search=）"""

    queryset = QaRecord.objects.select_related('user', 'note')
    serializer_class = QaRecordSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = super().get_queryset()
        keyword = (self.request.query_params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(question__icontains=keyword)
        return qs


class AdminQaBatchDeleteView(APIView):
    """POST /api/admin/qa/batch_delete/ —— 批量删除问答记录（body: {ids: [...]}）
    名下反馈流水随 CASCADE 一并清除；返回实际删除条数。"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        ids = request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            return Response({'detail': '请先勾选要删除的记录'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            id_list = [int(x) for x in ids]
        except (TypeError, ValueError):
            return Response({'detail': '记录标识有误'}, status=status.HTTP_400_BAD_REQUEST)
        qs = QaRecord.objects.filter(pk__in=id_list)
        deleted = qs.count()
        qs.delete()
        return Response({'detail': f'已删除 {deleted} 条问答记录', 'deleted': deleted})


# ---------- 知识库缺口（无依据问题聚合，补充材料后闭环） ----------

class AdminKbGapListView(ListAPIView):
    """GET /api/admin/kb/gaps/?status= —— 知识库缺口列表（模型默认按出现次数降序）"""

    serializer_class = KnowledgeGapSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = KnowledgeGap.objects.all()
        st = (self.request.query_params.get('status') or '').strip()
        if st:
            qs = qs.filter(status=st)
        return qs


class KbGapActionView(APIView):
    """POST /api/admin/kb/gaps/{id}/{resolve|ignore}/ —— 标记缺口已补充 / 已忽略"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk, action):
        gap = KnowledgeGap.objects.filter(pk=pk).first()
        if gap is None:
            return Response({'detail': '缺口不存在'}, status=status.HTTP_404_NOT_FOUND)
        if action == 'resolve':
            gap.status = KnowledgeGap.Status.RESOLVED
        elif action == 'ignore':
            gap.status = KnowledgeGap.Status.IGNORED
        else:
            return Response({'detail': '未知操作'}, status=status.HTTP_404_NOT_FOUND)
        gap.save(update_fields=['status', 'updated_at'])
        return Response(KnowledgeGapSerializer(gap).data)
