"""数据迁移：把使用说明新增的技巧 / FAQ 播种到「用户端」内容（scope=user）。

背景：用户端使用说明页的逻辑是「后端配置了就用配置，没配置才用代码内置默认」。
线上早已配置过旧内容，代码里新增的默认条目（批量生成、刷新保现场、停止取消、
课内会话归档、看板个人口径等）不会自动出现 —— 靠这个迁移幂等补齐：
- 同一 scope 已配置 → 只追加缺失的条目（按文本精确去重，管理员改过的旧条目不动）
- 同一 scope 为空 → 补全整套（管理员以后新增第一条时，前端兜底内容不会突然消失）
跑完后管理端工作台可继续对这些行做增删改排序。

Co-Authored-By: Claude Code <noreply@anthropic.com>
"""
from django.db import migrations

# 与用户端 GuidePage.vue 的 DEFAULT_TIPS 保持一致（新增 5 条在末尾）
TIPS = [
    '答案中的 [1][2] 是引用角标，点开“引用来源”可核对录音原文或板书原图，不怕 AI 编造',
    '纪要里的「录音 03:24」角标点开可查看该时间点的转写原文，复习时精准回听',
    '智能问答输入框支持语音提问与附件上传（图片 / PPT / 文档）；Enter 发送、Shift+Enter 换行',
    '视频材料会自动抽帧并去重，只保留信息量最大的关键帧，无需手动截屏或调参',
    '练习中做错的题会自动收进错题本，点开错题卡片可回看完整题目与解析，按章节重练巩固薄弱点',
    '多选文件或整个文件夹一次拖入，逐份设置主题与课程后可批量排队生成多份纪要；点左侧卡片随时切换查看每一份的进度与结果',
    '生成中途刷新浏览器也不怕：上传队列与生成清单会自动恢复现场，继续接着生成',
    '点「停止生成」会同时取消还在排队的任务；生成中的纪要点击「重新生成」可整体重跑',
    '在纪要页对某份材料提问，问答侧栏会自动把这节课归档为课内会话，按课程查找历史提问更方便',
    '数据看板的热门追问词云由你真实提问命中的知识片段聚合而来，标签全部是完整短语，配合高频知识点排行定位薄弱点',
]

# 与用户端 GuidePage.vue 的 DEFAULT_FAQS 保持一致（新增 3 条在末尾）
FAQS = [
    ('引用里的“录音 03:24”是怎么来的？',
     '语音转写模型会为每句话打上时间戳，纪要生成时把结论与其出处对齐，因此每个知识点都能定位到录音的精确时间点，点开角标即可核对原文。'),
    ('视频太大，浏览器会卡吗？',
     '视频在浏览器端通过 ffmpeg.wasm 抽帧，只把关键帧和音轨送往后端，传输量远小于原视频。建议单个视频不超过 20 分钟；过长可先剪辑或只上传录音。'),
    ('为什么纪要显示“处理失败”？',
     '常见原因：音频文件损坏、视频编码不受支持或网络中断。可在「我的纪要」中点击「重新处理」重试；若多次失败，请通过「问题反馈」联系我们。'),
    ('一节课的纪要包含哪些内容？',
     '按「课程主题、核心知识点、重要定义与公式、重点问题」等板块生成；课堂中没有的内容会自动省略。处理参数由系统统一按最佳配置运行，无需手动调整。'),
    ('我的课堂数据安全吗？',
     '录音、图片与纪要仅存储在你的课堂知识库中，不会参与任何模型训练；只有你本人能看到自己的课堂内容，可随时删除。'),
    ('一次上传很多文件，怎么生成多份纪要？',
     '多选文件、整个文件夹或 zip 压缩包都会进入上传队列，逐份设置课堂主题与课程后点「开始生成」，系统会按队列依次生成多份相互独立的纪要；期间可点击卡片切换查看各自的进度与内容，点「停止生成」会取消还在排队的任务。'),
    ('为什么问答侧栏会出现课内会话条目？',
     '在纪要页对某份材料提问过，侧栏才会把这节课归档为课内会话（有真实提问记录才显示，单纯生成纪要不会产生会话）；删除会话后从对应纪要再次提问，会话会自动重建。'),
    ('数据看板上的数字和别人看到的一样吗？',
     '不一样。看板展示的是你本人的学习数据（课堂、问答、练习与错题），每个人各看各的；管理员打开自己的看板看到的同样是管理员账号自己的数据。'),
]


def _next_order(model, scope: str) -> int:
    rows = list(model.objects.filter(scope=scope).order_by('-order').values_list('order', flat=True)[:1])
    return (rows[0] if rows else 0)


def seed_guide_defaults(apps, schema_editor):
    Tip = apps.get_model('faq', 'Tip')
    Faq = apps.get_model('faq', 'Faq')

    existing = {t.strip() for t in Tip.objects.filter(scope='user').values_list('text', flat=True)}
    order = _next_order(Tip, 'user')
    for text in TIPS:
        if text.strip() in existing:
            continue
        order += 1
        Tip.objects.create(scope='user', text=text, order=order)

    existing_q = {q.strip() for q in Faq.objects.filter(scope='user').values_list('q', flat=True)}
    order = _next_order(Faq, 'user')
    for q, a in FAQS:
        if q.strip() in existing_q:
            continue
        order += 1
        Faq.objects.create(scope='user', q=q, a=a, order=order)


class Migration(migrations.Migration):

    dependencies = [
        ('faq', '0003_alter_faq_scope_alter_tip_scope'),
    ]

    operations = [
        migrations.RunPython(seed_guide_defaults, migrations.RunPython.noop),
    ]
