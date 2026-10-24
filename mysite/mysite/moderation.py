"""
内容安全门禁（三层设计，普通问题零延迟）：
1. 词库预筛（正则，0 成本）：高置信违规内容直接拦截；健康词 / 政治词命中进下层；
2. flash 分类（仅半命中时一次调用）：政治类区分教科书事实题（放行走正常 RAG）与
   争议立场题（固定文案引导回学习范围）；
3. 健康链路：健康自述 → extract_health 结构化建档；涉饮食 / 用药建议且档案非空 →
   禁忌比对，命中即强制阻断（固定安全警告，不生成建议内容）。
LLM 失败一律 fail-open（放行 / 不阻断 / 不建档），审核组件故障绝不影响正常问答。
"""
import json
import re

from mysite import llm

# ---------- 固定文案（代码拼装，不经模型生成，保证口径稳定） ----------

BLOCKED_MESSAGE = (
    '这条内容我没办法回答～ 智学纪要是课堂学习助手，'
    '你可以问问课程知识点、课堂纪要里的内容，或者让我帮你出几道练习题。'
)

POLITICAL_REDIRECT = (
    '这个话题涉及立场性的讨论，不太适合由我来展开，我们先回到学习本身吧～\n'
    '可以问问课堂纪要里的知识点，或者让我针对课程内容出几道练习题。'
)

# 追加在健康相关回答末尾（views 检测「不构成医疗建议」子串防重复追加）
MEDICAL_DISCLAIMER = '\n\n—— 以上内容仅供参考，不构成医疗建议，具体请遵医嘱或咨询专业医生。'


# ---------- 词库（宁漏勿错：正则层只拦高置信内容，边缘情况交给模型层） ----------

# 高置信违规：暴力 / 毒品 / 色情 / 违法行为教唆 / 辱骂，命中即拦截
_BLOCK_PAT = re.compile(
    r'(?:枪支.{0,4}(?:制作|购买|买卖)|弹药|毒品|冰毒|摇头丸|吸毒方法|'
    r'自杀方法|怎么自杀|自残方法|'
    r'色情|裸聊|援交|嫖娼|卖淫|强奸|性侵|'
    r'黑客攻击教程|入侵(?:系统|服务器)教程|盗号教程|木马程序制作|'
    r'洗钱|诈骗(?:方法|教程)|代写论文|代考|替考|'
    r'傻逼|去死|畜生|废物东西|滚你妈)'
)

# 政治词半命中（不直接拦，交 flash 分类：事实题放行、立场题引导）
_POLITICAL_PAT = re.compile(
    r'(?:政治立场|政治观点|国家领导人|主席(?:怎么样|该不该|评价)|总统选举|'
    r'政党|执政党|在野党|台独|藏独|港独|疆独|'
    r'游行示威|颜色革命|意识形态(?:斗争|争论)|专制还是民主|'
    r'(?:共产党|国民党|政府)(?:好不好|怎么样|该不该|评价)|'
    r'政治(?:敏感|争议)(?:话题|事件)?(?:讨论|看法|站队)?)'
)

# 健康自述（建档触发）：第一人称的过敏史 / 用药 / 病史 / 饮食禁忌
_HEALTH_PAT = re.compile(
    r'(?:我对[^。？?！!，,]{1,10}过敏|过敏(?:史|体质|源)|花粉症|乳糖不耐受?|麸质(?:过敏|不耐)|'
    r'我(?:正在|在|长期|一直)?(?:吃|服用|注射|使用|涂|抹)[^。？?！!，,]{0,10}药|'
    r'(?:我|本人)[^。？?！!，,]{0,6}(?:哮喘|糖尿病|高血压|心脏病|胃炎|贫血|鼻炎|甲亢|甲减)|'
    r'(?:我|本人)(?:有|被诊断|确诊)[^。？?！!，,]{0,12}(?:病史|症|炎|病)|'
    r'我(?:对|有)?[^。？?！!，,]{0,8}忌口|我不能吃|我不吃[^。？?！!，,]{1,8}|'
    r'(?:医生|体检)(?:说我|显示)[^。？?！!，,]{0,15})'
)

# 求饮食 / 用药 / 健康建议（触发禁忌比对与免责声明）
_HEALTH_ADVICE_PAT = re.compile(
    r'(?:吃什么(?:好|药)?|食谱|菜谱|推荐[^。？?！!，,]{0,6}(?:餐|菜|食物|饮食|汤)|'
    r'(?:早|午|晚)餐|三餐|加餐|宵夜|'
    r'能不能吃|可以吃(?:吗|么)|适合吃|还能吃|忌(?:口|什么)|'
    r'剂量|用量|怎么用药|如何用药|用药建议|'
    r'养生|进补|保健品|维生素|钙片|减肥餐|增肌餐|降(?:血压|血糖)(?:食谱|吃什么))'
)


# ---------- 第 1、2 层：内容审核（违规拦截 / 政治引导） ----------

def moderate(question: str) -> dict:
    """内容审核门禁（每个提问都会调用，普通问题必须零成本）。
    返回 {'verdict': 'allow' | 'redirect' | 'block', 'category': str, 'message': str}。
    block → 直接拦截；redirect → 以 message 作为回答（政治引导）；allow → 正常问答链路。"""
    if _BLOCK_PAT.search(question):
        return {'verdict': 'block', 'category': 'blocked', 'message': BLOCKED_MESSAGE}
    if _POLITICAL_PAT.search(question):
        return _classify_political(question)
    return {'verdict': 'allow', 'category': '', 'message': ''}


def _classify_political(question: str) -> dict:
    """政治词半命中：flash 区分课程内事实题（放行）与争议立场题（引导）。失败放行。"""
    if not llm.available():
        return {'verdict': 'allow', 'category': '', 'message': ''}
    data = llm.chat_json(
        [{'role': 'system', 'content': (
            '你是学习平台的内容审核员，把学生问题分为两类：'
            'A 类：历史 / 政治 / 地理等课程范围内的知识性、事实性问题'
            '（事件经过、时间、原因、意义、制度内容介绍等）→ allow；'
            'B 类：征求立场评价、比好坏、站队、煽动对立或意识形态争论 → redirect。'
            '只输出 JSON：{"verdict": "allow" 或 "redirect"}')},
         {'role': 'user', 'content': question[:500]}],
        model=llm.MODEL_LITE, temperature=0.1, timeout=8,
    )
    if isinstance(data, dict) and data.get('verdict') == 'redirect':
        return {'verdict': 'redirect', 'category': 'political', 'message': POLITICAL_REDIRECT}
    return {'verdict': 'allow', 'category': '', 'message': ''}


# ---------- 第 3 层：健康档案（提取建档 + 禁忌阻断） ----------

def health_hit(question: str) -> bool:
    """是否含第一人称健康自述（过敏 / 用药 / 病史 / 禁忌）→ 触发建档提取。"""
    return bool(_HEALTH_PAT.search(question))


def health_advice_hit(question: str) -> bool:
    """是否在求饮食 / 用药 / 健康类建议 → 触发禁忌比对与免责声明追加。"""
    return bool(_HEALTH_ADVICE_PAT.search(question))


def extract_health(question: str) -> dict:
    """健康自述结构化抽取：4 类短条目（allergies / medications / conditions / diet_taboos）。
    无内容或失败返回 {}（调用方据此跳过建档）。"""
    if not llm.available():
        return {}
    data = llm.chat_json(
        [{'role': 'system', 'content': (
            '从学生的话里抽取「本人的」长期健康信息，归为四类，条目用简短名词'
            '（如：花生、青霉素、哮喘、海鲜、辣椒）：'
            'allergies=过敏史，medications=正在使用的药物，conditions=病史 / 慢病，'
            'diet_taboos=饮食禁忌。只抽取明确自述的内容，课堂知识性描述不要抽。'
            '只输出 JSON：{"allergies": [], "medications": [], "conditions": [], "diet_taboos": []}')},
         {'role': 'user', 'content': question[:500]}],
        model=llm.MODEL_LITE, temperature=0.1, timeout=10,
    )
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for key in ('allergies', 'medications', 'conditions', 'diet_taboos'):
        got = data.get(key)
        if not isinstance(got, list):
            continue
        items = [str(x).strip()[:30] for x in got if str(x).strip()]
        if items:
            out[key] = items[:10]
    return out


def contraindication_conflict(question: str, items: list[str]) -> list[str]:
    """禁忌比对：回答该问题是否会与档案条目冲突。
    只返回白名单内命中的条目（保留原大小写）；失败返回 []（fail-open 不阻断）。"""
    if not llm.available() or not items:
        return []
    data = llm.chat_json(
        [{'role': 'system', 'content': (
            '给定用户的健康档案条目和一个提问。若回答该提问（推荐食物 / 做法 / 用药等）'
            '会与某条档案条目冲突（如推荐含过敏原的食物、与在用药物相冲的 suggestions），'
            '把冲突的条目原文列出；无冲突给空列表。'
            '只输出 JSON：{"conflicts": ["条目原文"]}')},
         {'role': 'user', 'content': (
             f'健康档案条目：{json.dumps(items, ensure_ascii=False)}\n\n提问：{question[:500]}')}],
        model=llm.MODEL_LITE, temperature=0.1, timeout=10,
    )
    if not isinstance(data, dict) or not isinstance(data.get('conflicts'), list):
        return []
    whitelist = {str(x).strip().lower(): str(x).strip() for x in items if str(x).strip()}
    out: list[str] = []
    for raw in data['conflicts']:
        hit = whitelist.get(str(raw).strip().lower())
        if hit and hit not in out:
            out.append(hit)
    return out


def health_block_message(conflict_items: list[str]) -> str:
    """禁忌命中的固定安全警告：点名冲突条目，不给出任何建议内容。"""
    names = '、'.join(conflict_items[:3])
    return (
        f'根据你健康档案里的记录（{names}），这个请求可能带来健康风险，我先停下了。\n'
        '涉及饮食与用药请以医生嘱咐为准；如果情况有变，'
        '可以在「个人设置」里更新你的健康档案，我会按新档案来判断。'
    )
