"""
题库接口：
- 用户端：按课程 / 章节 / 题型刷题、基于纪要 LLM 出题（LLM 失败降级规则版）、提交批改、错题本
- 管理端：全部题目巡检
"""
import re

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mysite import llm

from mysite.utils import IsAdminRole
from notes.models import Note

from .models import QuizItem, WrongRecord
from .serializers import (
    QuizDetailSerializer, QuizItemSerializer, SubmitSerializer, WrongRecordSerializer,
)


def _normalize(text: str) -> str:
    """答案归一化：去空白、去分隔符、转大写，比较时忽略顺序差异的干扰字符"""
    return re.sub(r'[\s,，、;；]+', '', (text or '')).upper()


def _is_correct(quiz: QuizItem, my_answer: str) -> bool:
    """批改：选择题比较选项字母集合；判断 / 填空 / 简答比较归一化文本"""
    mine = _normalize(my_answer)
    truth = _normalize(quiz.answer)
    if quiz.type in (QuizItem.Type.SINGLE, QuizItem.Type.MULTIPLE,
                     QuizItem.Type.INDEFINITE, QuizItem.Type.JUDGE):
        return sorted(mine) == sorted(truth)
    return mine == truth or (mine and (mine in truth or truth in mine))


class QuizListView(ListAPIView):
    """GET /api/quiz/ —— 本人题库列表（?course=&chapter=&type=&search=）
    只返回本人纪要生成的题目：练习页出题范围按本人课程 / 纪要展开，
    串库会把别人的题混进空范围会话（无纪要账号也能凭他人题目出卷）。"""

    queryset = QuizItem.objects.select_related('uploader', 'note')
    serializer_class = QuizItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset().filter(uploader=self.request.user)
        params = self.request.query_params
        for field in ('course', 'chapter', 'type'):
            value = params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        keyword = (params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(stem__icontains=keyword)
        return qs


class QuizGenerateView(APIView):
    """
    POST /api/quiz/generate/ —— 基于纪要 LLM 出题入库
    body: {note_id, count?, types?, difficulties?}
    （count 默认 5 上限 10；types 限定题型，兼容前端别名 multi/short；
    difficulties 限定难度子集如 ["简单","困难"]，缺省 / 空表示不限——「全部」）
    真实出题：纪要片段交给 qwen-plus，按要求生成 单选 / 多选 / 不定项 / 判断 / 填空 / 简答，
    题目带难度、来源引用（可对齐到录音时间点）与知识点标签；每道题都过结构校验，
    不合格直接丢弃。LLM 不可用 / 有效题目不足时降级规则版（定义→填空、知识点→简答）。
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        note = Note.objects.filter(
            pk=request.data.get('note_id'), uploader=request.user,
        ).first()
        if note is None:
            return Response({'detail': '请选择自己的一节已完成纪要'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            count = min(max(int(request.data.get('count') or 5), 1), 10)
        except (TypeError, ValueError):
            count = 5
        want = _normalize_types(request.data.get('types'))
        want_diff = _normalize_difficulties(request.data.get('difficulties'))
        created = _llm_generate(note, request.user, count, want, want_diff) \
            or _rule_generate(note, request.user, count, want_diff)
        if not created:
            return Response({'detail': '该纪要暂无可出题内容'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(QuizItemSerializer(created, many=True).data, status=status.HTTP_201_CREATED)


# ---------- 出题：题型归一 / 材料拼装 / LLM 生成与校验 / 规则降级 ----------

_TYPE_ALIASES = {'multi': QuizItem.Type.MULTIPLE, 'short': QuizItem.Type.ANSWER}
_JUDGE_NORMALIZE = {
    '对': '对', '正确': '对', 't': '对', 'true': '对', 'a': '对',
    '错': '错', '错误': '错', 'f': '错', 'false': '错', 'b': '错',
}
_QUIZ_SYSTEM = (
    '你是课程题库编辑。根据给定的课堂纪要材料出练习题，只输出 JSON：'
    '{"questions": [{"type": "single|multiple|indefinite|judge|blank|answer",'
    ' "difficulty": "简单|中等|困难",'
    ' "stem": "题干",'
    ' "options": ["A. 选项内容", "B. 选项内容", "C. 选项内容", "D. 选项内容"],'
    ' "answer": "参考答案", "analysis": "解析", "cite": "来源引用", "knowledge": "知识点标签"}]}'
    '要求：'
    '1. 题目必须考查材料里的内容，不要出材料中没有的知识点；'
    '2. single/multiple/indefinite 必须给 4 个带 A. B. C. D. 前缀的选项，answer 用选项字母'
    '（multiple/indefinite 多个字母连写如 "ABD"）；'
    '3. judge 的 answer 只能是 "对" 或 "错"；blank 的题干用 ________ 表示空位，answer 为空位的简短答案；'
    '4. answer（简答）的 answer 为参考答案要点；'
    '5. cite 填该题考查内容的来源引用，材料中带（录音 mm:ss）引用时必须原样使用；'
    '6. knowledge 填 2~6 字知识点标签；analysis 说明正确答案依据。'
)


def _normalize_types(raw) -> list[str] | None:
    """题型过滤条件归一：兼容前端别名（multi→multiple / short→answer），过滤非法值"""
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    for t in raw:
        t = _TYPE_ALIASES.get(str(t).strip(), str(t).strip())
        if t in QuizItem.Type.values:
            out.append(t)
    return list(dict.fromkeys(out)) or None


def _normalize_difficulties(raw) -> list[str] | None:
    """难度过滤条件归一：只保留 简单/中等/困难，缺省 / 空 / 全非法视为不限难度"""
    if not isinstance(raw, list):
        return None
    out = [d for d in dict.fromkeys(str(x).strip() for x in raw) if d in ('简单', '中等', '困难')]
    return out or None


def _quiz_material(note: Note) -> str:
    """把纪要压成出题材料：主题 / 知识点 / 定义 / 重点 / 转写摘录（带时间戳引用）"""
    lines = [f'课程：{note.course}', f'课堂主题：{note.theme or note.title}']
    points = [p.get('text', '') for p in (note.points or []) if p.get('text')]
    if points:
        lines.append('知识点：' + '；'.join(points))
    defs = [f"{d.get('term', '')}：{d.get('text', '')}（{d.get('ref') or '纪要定义'}）"
            for d in (note.definitions or []) if d.get('term')]
    if defs:
        lines.append('重要定义与公式：' + '；'.join(defs))
    if note.emphasized:
        lines.append('老师强调的重点：' + '；'.join(note.emphasized))
    transcript = [line.strip() for line in (note.transcript or '').split('\n') if line.strip()][:40]
    if transcript:
        lines.append('转写摘录：\n' + '\n'.join(transcript))
    return '\n'.join(lines)[:6000]


def _valid_item(item: dict) -> dict | None:
    """单题结构校验：题型合法、选择题选项/答案字母自洽、判断题归一对错；不合格返回 None"""
    qtype = str(item.get('type') or '').strip()
    if qtype not in QuizItem.Type.values:
        return None
    stem = str(item.get('stem') or '').strip()
    answer = str(item.get('answer') or '').strip()
    if not stem or not answer:
        return None
    difficulty = str(item.get('difficulty') or '').strip()
    if difficulty not in ('简单', '中等', '困难'):
        difficulty = '中等'

    options: list[str] = []
    if qtype in (QuizItem.Type.SINGLE, QuizItem.Type.MULTIPLE, QuizItem.Type.INDEFINITE):
        raw = item.get('options')
        if not isinstance(raw, list):
            return None
        texts = [re.sub(r'^[A-F]\s*[\.、．]\s*', '', str(o)).strip() for o in raw]
        options = [f'{"ABCDEF"[i]}. {t}' for i, t in enumerate(texts) if t][:6]
        if len(options) < 2:
            return None
        letters = sorted(set(re.findall(r'[A-F]', answer.upper())))
        if not letters or any('ABCDEF'.index(l) >= len(options) for l in letters):
            return None
        if qtype == QuizItem.Type.SINGLE and len(letters) != 1:
            return None
        if qtype == QuizItem.Type.MULTIPLE and len(letters) < 2:
            return None
        answer = ''.join(letters)
    elif qtype == QuizItem.Type.JUDGE:
        answer = _JUDGE_NORMALIZE.get(answer if len(answer) > 2 else answer.lower(), '')
        if not answer:
            return None
    return {
        'type': qtype, 'difficulty': difficulty, 'stem': stem, 'options': options,
        'answer': answer, 'analysis': str(item.get('analysis') or '').strip(),
        'cite': str(item.get('cite') or '').strip()[:50],
        'knowledge': str(item.get('knowledge') or '').strip()[:100],
    }


def _llm_generate(note: Note, user, count: int, want: list[str] | None,
                  want_diff: list[str] | None) -> list[QuizItem]:
    """LLM 出题：材料 + 题型 / 难度要求交给 qwen-plus，逐题校验入库；失败 / 不足返回已生成部分"""
    type_hint = (f'题型限定为：{"、".join(QuizItem(t).get_type_display() for t in want)}。'
                 if want else '题型尽量覆盖单选 / 多选 / 判断 / 填空，以客观题为主。')
    # 限定难度子集时明确要求按材料真实评估并只取这些档位；不限时维持「中等为主」的分布提示。
    # （此前提示词写死中等为主且前端不传难度，导致无论怎么选都只出中等题——2026-10-27 用户反馈）
    if want_diff:
        diff_hint = (f'难度限定为：{"、".join(want_diff)}。每道题的 difficulty 必须从这些值中选取，'
                     f'并根据材料内容真实评估各题难度，不要全部标成同一档。')
    else:
        diff_hint = '难度以中等为主、少量简单和困难。'
    # 难度子集下校验会丢弃标错档位的题，多要几道补足数量
    ask_count = count + 3 if want_diff else count
    data = llm.chat_json(
        [{'role': 'system', 'content': _QUIZ_SYSTEM},
         {'role': 'user', 'content': f'课堂纪要材料：\n{_quiz_material(note)}\n\n'
                                     f'请出 {ask_count} 道题，{type_hint}{diff_hint}'}],
        temperature=0.5,
        timeout=90,   # 9~13 道题的 JSON 输出实测约 30s，默认 30s 超时会让整批出题静默失败
    )
    if not isinstance(data, dict) or not isinstance(data.get('questions'), list):
        return []
    created, seen = [], set()
    for item in data['questions']:
        if len(created) >= count:
            break
        if not isinstance(item, dict):
            continue
        fields = _valid_item(item)
        if fields is None or fields['stem'] in seen:
            continue
        if want_diff and fields['difficulty'] not in want_diff:
            continue   # 难度子集硬过滤：模型标错档位的题不入库
        seen.add(fields['stem'])
        created.append(QuizItem.objects.create(
            uploader=user, note=note, course=note.course, chapter=note.title, **fields,
        ))
    return created


def _rule_generate(note: Note, user, count: int, want_diff: list[str] | None = None) -> list[QuizItem]:
    """规则版降级：定义→填空、知识点→简答（带来源引用），保证接口闭环不依赖外网。
    规则题难度固定为中等：难度子集不含中等时直接不出（宁可空缺也不虚标难度）。"""
    if want_diff and '中等' not in want_diff:
        return []
    created: list[QuizItem] = []
    for d in (note.definitions or [])[:count]:
        created.append(QuizItem.objects.create(
            note=note, course=note.course, chapter=note.title,
            type=QuizItem.Type.BLANK,
            stem=f'请补充：{d.get("term", "")}指的是________（来源：{note.title}）。',
            answer=d.get('text', ''),
            analysis=f'该定义出自纪要「{note.title}」的重要定义与公式板块。',
            difficulty='中等', cite=d.get('ref') or '纪要定义', knowledge=d.get('term', ''),
            uploader=user,
        ))
    for p in (note.points or [])[:count - len(created)]:
        created.append(QuizItem.objects.create(
            note=note, course=note.course, chapter=note.title,
            type=QuizItem.Type.ANSWER,
            stem=f'简述：{p.get("text", "")}（来源：{note.title}）',
            answer=p.get('text', ''),
            analysis='参考答案即该知识点的完整表述，可对照纪要原文复盘。',
            difficulty='中等', cite=p.get('ref') or '纪要知识点', knowledge=(p.get('text') or '')[:12],
            uploader=user,
        ))
    return created


class QuizSubmitView(APIView):
    """POST /api/quiz/{id}/submit/ —— 提交作答，即时批改（错题自动进错题本）"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        quiz = get_object_or_404(QuizItem, pk=pk)
        ser = SubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        my_answer = ser.validated_data['answer']

        correct = _is_correct(quiz, my_answer)
        if correct:
            WrongRecord.objects.filter(user=request.user, quiz=quiz).delete()
        else:
            WrongRecord.objects.update_or_create(
                user=request.user, quiz=quiz, defaults={'my_answer': my_answer},
            )
        return Response({'correct': correct, 'my_answer': my_answer, **QuizDetailSerializer(quiz).data})


class WrongListView(ListAPIView):
    """GET /api/quiz/wrong/ —— 我的错题本"""

    serializer_class = WrongRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return WrongRecord.objects.filter(user=self.request.user).select_related('quiz')


class WrongDeleteView(APIView):
    """DELETE /api/quiz/wrong/{id}/ —— 已掌握，从错题本移除"""

    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        record = WrongRecord.objects.filter(pk=pk, user=request.user).first()
        if record is None:
            return Response({'detail': '记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        record.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminQuizListView(ListAPIView):
    """GET /api/admin/quiz/ —— 全部题目巡检（?course=&type=&search=）"""

    queryset = QuizItem.objects.select_related('uploader', 'note')
    serializer_class = QuizItemSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        for field in ('course', 'type'):
            value = params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        keyword = (params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(stem__icontains=keyword)
        return qs


class AdminQuizBatchDeleteView(APIView):
    """POST /api/admin/quiz/batch_delete/ —— 批量删除题目（body: {ids: [...]}）
    题目名下错题记录随外键级联一并清除；返回实际删除条数。"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        ids = request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            return Response({'detail': '请先勾选要删除的题目'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            id_list = [int(x) for x in ids]
        except (TypeError, ValueError):
            return Response({'detail': '记录标识有误'}, status=status.HTTP_400_BAD_REQUEST)
        qs = QuizItem.objects.filter(pk__in=id_list)
        deleted = qs.count()
        qs.delete()
        return Response({'detail': f'已删除 {deleted} 道题目', 'deleted': deleted})
