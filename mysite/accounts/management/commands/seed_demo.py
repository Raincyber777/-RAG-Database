"""
演示数据初始化：python manage.py seed_demo [--force]
写入与两端前端演示一致的账号（超级管理员 / 管理员 / 普通用户）、
常见问题、课堂纪要、问答、练习题与反馈示例，迁移后一键可演示。
"""
from django.core.management.base import BaseCommand

from accounts.models import User
from faq.models import Faq, Tip
from feedback.models import Feedback
from notes.models import Note
from qa.models import QaRecord
from quiz.models import QuizItem, WrongRecord

USERS = [
    # (用户名, 手机号, 邮箱, 密码, 角色, 状态)
    ('超级管理员', '13800000001', 'admin@zhiyuan.ai', 'admin123456', 'super', 'active'),
    ('陈教务', '13800000002', 'chenjw@zhiyuan.ai', 'admin123456', 'admin', 'active'),
    ('刘运营', '13800000003', 'liuyy@zhiyuan.ai', 'admin123456', 'admin', 'active'),
    ('周实习', '13800000004', 'zhousx@zhiyuan.ai', 'admin123456', 'admin', 'disabled'),
    ('林小智', '13800001001', 'linxz@zhiyuan.ai', '123456', 'user', 'active'),
    ('陈雨桐', '13800001002', 'chyt@zhiyuan.ai', '123456', 'user', 'active'),
    ('王梓萌', '13800001003', 'wzmm@zhiyuan.ai', '123456', 'user', 'active'),
    ('李思远', '13800001004', 'lsy@zhiyuan.ai', '123456', 'user', 'active'),
    ('赵一诺', '13800001005', 'zyn@zhiyuan.ai', '123456', 'user', 'disabled'),
    ('周子航', '13800001006', 'zzh@zhiyuan.ai', '123456', 'user', 'active'),
    ('吴佳琪', '13800001007', 'wjq@zhiyuan.ai', '123456', 'user', 'active'),
    ('郑博文', '13800001008', 'zbw@zhiyuan.ai', '123456', 'user', 'disabled'),
]

FAQS = [
    ('user', '引用里的“录音 03:24”是怎么来的？',
     '语音转写模型会为每句话打上时间戳，纪要生成时把结论与其出处对齐，因此每个知识点都能定位到录音的精确时间点，点开角标即可核对原文。'),
    ('user', '视频太大，浏览器会卡吗？',
     '视频在浏览器端通过 ffmpeg.wasm 抽帧，只把关键帧和音轨送往后端，传输量远小于原视频。建议单个视频不超过 20 分钟；过长可先剪辑或只上传录音。'),
    ('user', '为什么纪要显示“处理失败”？',
     '常见原因：音频文件损坏、视频编码不受支持或网络中断。可在「我的纪要」中点击「重新处理」重试；若多次失败，请通过「问题反馈」联系我们。'),
    ('user', '一节课的纪要包含哪些内容？',
     '按「课程主题、核心知识点、重要定义与公式、重点问题」等板块生成；课堂中没有的内容会自动省略。处理参数由系统统一按最佳配置运行，无需手动调整。'),
    ('user', '我的课堂数据安全吗？',
     '录音、图片与纪要仅存储在你的课堂知识库中，不会参与任何模型训练；只有你本人能看到自己的课堂内容，可随时删除。'),
    ('admin', '普通用户账号可以登录管理端吗？',
     '不可以。两端共用用户端首页的登录表单：普通用户账号登录后只会进入用户端；管理员 / 超级管理员账号登录后先进入管理端工作台，可在工作台一键进入用户端。'),
    ('admin', '管理员账号由谁创建？密码忘了怎么办？',
     '管理员账号统一由超级管理员在「账号管理」页创建，初始密码由超管线下告知；忘记密码时联系超级管理员重置，重置后可自行在个人设置中修改。'),
    ('admin', '停用管理员 / 用户账号会影响他们的数据吗？',
     '不会。停用仅禁止登录，账号名下的纪要、问答、题库数据全部保留；恢复后一切如常。删除类操作（如删除课堂纪要）才不可恢复，需谨慎确认。'),
    ('admin', '数据看板上的数字是真实的吗？',
     '当前为演示环境，看板指标、热门追问词与最新动态均来自前端演示数据：热门追问词实时统计自问答记录文本，最新动态每 3.5 秒轮播一次。接入后端后由统计接口提供。'),
    # home：用户端首页（着陆页）的常见问题
    ('home', '纪要里的「录音 03:24」是怎么来的？',
     '语音转写模型会为每句话打上时间戳，纪要生成时把结论与其出处对齐，因此每个知识点都能定位到录音的精确时间点，点开角标即可核对原文。'),
    ('home', '视频太大，浏览器会卡吗？',
     '视频在浏览器端通过 ffmpeg.wasm 抽帧，只把关键帧和音轨送往后端，传输量远小于原视频。建议单个视频不超过 20 分钟；过长可先剪辑或只上传录音。'),
    ('home', '我的课堂数据安全吗？',
     '录音、图片与纪要仅存储在你的课堂知识库中，不会参与任何模型训练；只有你本人能看到自己的课堂内容，可随时删除。'),
]

TIPS = [
    # (scope, 技巧内容) —— 与两端「使用说明」页的使用技巧一致
    ('user', '答案中的 [1][2] 是引用角标，点开“引用来源”可核对录音原文或板书原图，不怕 AI 编造'),
    ('user', '纪要里的「录音 03:24」角标点开可查看该时间点的转写原文，复习时精准回听'),
    ('user', '智能问答输入框支持语音提问与附件上传（图片 / PPT / 文档）；Enter 发送、Shift+Enter 换行'),
    ('user', '视频材料会自动抽帧并去重，只保留信息量最大的关键帧，无需手动截屏或调参'),
    ('user', '练习中做错的题会自动收进错题本，点开错题卡片可回看完整题目与解析，按章节重练巩固薄弱点'),
    ('admin', '演示环境下手机号 / 邮箱验证码统一为 123456；重置密码至少 8 位。'),
    ('admin', '停用账号只禁止登录、不删除数据，随时可以恢复；操作前会弹出二次确认。'),
    ('admin', '管理员账号两端通用：在用户端登录会先进入管理端，工作台可一键回到用户端。'),
    ('admin', '用户名修改后，欢迎语的称呼、侧边栏卡片会同步更新（重新登录同样生效）。'),
    ('admin', '看板图表与动态为演示数据，接入后端后替换为统计接口即可，页面结构不变。'),
]

NOTES = [
    # (课程, 主题, 时长, 知识点, 定义, 重点)
    ('高等数学', '第11讲 数列极限与收敛判定', '45:12',
     ['夹逼定理可处理左右极限相等的情形', '单调有界数列必收敛', 'ε-N 语言刻画极限'],
     [{'term': '数列极限', 'text': '对任意 ε>0，存在 N，当 n>N 时 |aₙ-A|<ε', 'ref': '录音 12:08'}],
     ['收敛与有界的关系：收敛必有界，反之不成立']),
    ('高等数学', '第12讲 泰勒展开与级数', '47:33',
     ['泰勒公式含皮亚诺余项与拉格朗日余项', '麦克劳林展开是泰勒展开在 x=0 处的特例', '交错级数用莱布尼茨判别法'],
     [{'term': '莱布尼茨判别法', 'text': '交错级数若通项单调递减趋于 0 则收敛', 'ref': '录音 31:40'}],
     ['注意：莱布尼茨判别法的条件是「单调递减」']),
    ('操作系统', '第5讲 进程与线程', '43:05',
     ['进程是资源分配的基本单位', '线程是调度执行的基本单位', '进程切换开销大于线程切换'],
     [{'term': '进程', 'text': '程序在某个数据集合上的一次运行活动', 'ref': '录音 05:22'}],
     ['死锁四个必要条件缺一不可']),
    ('数据结构', '第8讲 二叉树与遍历', '41:56',
     ['先序 / 中序 / 后序遍历的递归与非递归实现', '层序遍历借助队列', '已知先序 + 中序可唯一重建二叉树'],
     [{'term': '二叉树', 'text': '每个结点至多有两棵子树的树结构', 'ref': '板书 第2页'}],
     ['考点：根据遍历序列重建二叉树']),
]

QUIZZES = [
    ('高等数学', '第11讲 数列极限与收敛判定', 'single', '数列 {aₙ} 收敛是 {aₙ} 有界的：', ['充分条件', '必要条件', '充要条件', '既不充分也不必要'], 'B', '收敛必有界；有界不一定收敛（如 aₙ=(-1)ⁿ）。'),
    ('高等数学', '第12讲 泰勒展开与级数', 'indefinite', '关于麦克劳林展开，下列说法成立的有：', ['e^x 展开式中各项系数为 1/n!', 'sin x 展开式只含奇数次幂', 'ln(1+x) 在 x=-1 处也收敛', '展开阶数越高近似误差越小（收敛域内）'], 'ABD', 'ln(1+x) 的收敛域为 (-1,1]，在 x=-1 处发散。'),
    ('操作系统', '第5讲 进程与线程', 'single', '进程从运行态转为阻塞态的原因是：', ['时间片用完', '进程主动请求 I/O', '高优先级进程到达', '进程执行结束'], 'B', '时间片用完是转为就绪态；阻塞由进程主动等待事件引起。'),
    ('数据结构', '第8讲 二叉树与遍历', 'single', '已知先序 ABDC、中序 BDAC，则后序序列为：', ['DBCA', 'DBAC', 'DCBA', 'BCDA'], 'A', '由先序定根、中序分左右，递归重建后得后序 DBCA。'),
]

FEEDBACKS = [
    ('李思远', '导出的纪要 PDF 里数学公式乱码', '导出的纪要 PDF 里数学公式显示乱码，Markdown 导出正常，麻烦排查一下。', 'pending'),
    ('陈雨桐', '希望录音转写能边处理边预览', '希望上传录音后能边处理边预览转写文字，不用等纪要全部生成完。', 'pending'),
    ('王梓萌', '上传超过 20 分钟的视频提示失败', '上传超过 20 分钟的课堂视频会提示失败，错误信息只有「处理异常」，希望能提示更具体。', 'pending'),
    ('林小智', '「第12讲 泰勒展开与级数」纪要内容有误', '纪要里把莱布尼茨判别法的条件写成了「单调递增」，应为单调递减。', 'resolved'),
    ('周子航', '纪要数据是否会用于模型训练', '想了解一下纪要数据会不会被用于模型训练，希望给个明确说明。', 'resolved'),
]


class Command(BaseCommand):
    help = '写入演示数据（账号 / FAQ / 使用技巧 / 纪要 / 问答 / 题库 / 反馈）'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='已有数据时仍然重写（先清空）')

    def handle(self, *args, **options):
        if User.objects.filter(email='admin@zhiyuan.ai').exists() and not options['force']:
            self.stdout.write(self.style.WARNING('演示数据已存在，跳过（如需重写请加 --force）'))
            return
        if options['force']:
            WrongRecord.objects.all().delete()
            QuizItem.objects.all().delete()
            QaRecord.objects.all().delete()
            Note.objects.all().delete()
            Feedback.objects.all().delete()
            Faq.objects.all().delete()
            Tip.objects.all().delete()
            User.objects.all().delete()

        # ---- 账号 ----
        for name, phone, email, pwd, role, status in USERS:
            User.objects.create_user(
                username=name, phone=phone, email=email, password=pwd,
                role=role, status=status,
            )
        self.stdout.write(f'账号 x{len(USERS)}')

        # ---- FAQ ----
        for order, (scope, q, a) in enumerate(FAQS):
            Faq.objects.create(scope=scope, q=q, a=a, order=order)
        self.stdout.write(f'FAQ x{len(FAQS)}')

        # ---- 使用技巧 ----
        for order, (scope, text) in enumerate(TIPS):
            Tip.objects.create(scope=scope, text=text, order=order)
        self.stdout.write(f'使用技巧 x{len(TIPS)}')

        # ---- 纪要（挂在 林小智 名下） ----
        lin = User.objects.get(email='linxz@zhiyuan.ai')
        notes = []
        for course, title, duration, points, defs, emphasized in NOTES:
            notes.append(Note.objects.create(
                course=course, title=title, duration=duration, uploader=lin,
                status=Note.Status.DONE,
                theme=f'本节课围绕「{title.split(" ", 1)[-1]}」展开，梳理了核心概念与典型例题。',
                points=[{'text': p, 'ref': '录音'} for p in points],
                definitions=defs, emphasized=emphasized,
                transcript=f'（{course}·{title} 的课堂转写原文占位，接入 ASR 后为逐句带时间戳文本）',
            ))
        self.stdout.write(f'纪要 x{len(notes)}')

        # ---- 问答 ----
        qa_data = [
            ('夹逼定理什么时候用？', 0, ['录音 08:15']),
            ('莱布尼茨判别法的条件是什么？', 1, ['录音 31:40']),
            ('进程和线程的主要区别？', 2, ['录音 10:02']),
            ('先序和中序怎么重建二叉树？', 3, ['板书 第3页']),
        ]
        records = []
        for question, note_i, refs in qa_data:
            note = notes[note_i]
            records.append(QaRecord.objects.create(
                user=lin, note=note, question=question,
                answer=f'根据《{note.title}》的纪要内容，该问题的结论可在引用来源处核对原文。',
                references=[{'label': note.title, 'time': t, 'detail': ''} for t in refs],
            ))
        records[0].liked = True
        records[0].save()
        self.stdout.write(f'问答 x{len(records)}')

        # ---- 题库 + 错题 ----
        quizzes = []
        for course, chapter, qtype, stem, options, answer, analysis in QUIZZES:
            quizzes.append(QuizItem.objects.create(
                note=notes[0] if course == '高等数学' else next(
                    n for n in notes if n.course == course),
                course=course, chapter=chapter, type=qtype, stem=stem,
                options=options, answer=answer, analysis=analysis, uploader=lin,
            ))
        WrongRecord.objects.create(user=lin, quiz=quizzes[1], my_answer='ABC')
        self.stdout.write(f'题目 x{len(quizzes)}（含错题 1 条）')

        # ---- 反馈 ----
        for name, title, content, fstatus in FEEDBACKS:
            user = User.objects.get(username=name)
            Feedback.objects.create(
                user=user, title=title, content=content,
                contact=user.email, status=fstatus,
            )
        self.stdout.write(f'反馈 x{len(FEEDBACKS)}')

        self.stdout.write(self.style.SUCCESS('演示数据初始化完成。管理员登录：admin@zhiyuan.ai / admin123456'))
