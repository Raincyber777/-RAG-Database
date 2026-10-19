"""
平台 / 个人数据速览接口（工作台与数据看板用），按角色返回不同口径：
- 普通用户：自己的纪要 / 问答 / 练习 / 错题数量
- 管理员·超管：平台用户数 / 纪要数 / 问答数 / 待处理反馈数
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from feedback.models import Feedback
from notes.models import Note
from qa.models import QaRecord
from quiz.models import QuizItem, WrongRecord


class StatsSummaryView(APIView):
    """GET /api/stats/summary/ —— 数据速览"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.is_admin_role:
            return Response({
                'scope': 'platform',
                'users': user.__class__.objects.filter(role='user').count(),
                'notes': Note.objects.count(),
                'qa_records': QaRecord.objects.count(),
                'pending_feedbacks': Feedback.objects.filter(status=Feedback.Status.PENDING).count(),
                'quiz_items': QuizItem.objects.count(),
            })
        return Response({
            'scope': 'personal',
            'notes': Note.objects.filter(uploader=user).count(),
            'qa_records': QaRecord.objects.filter(user=user).count(),
            'quiz_items': QuizItem.objects.filter(uploader=user).count(),
            'wrong_records': WrongRecord.objects.filter(user=user).count(),
        })


class DashboardFeedView(APIView):
    """GET /api/stats/dashboard/ —— 管理端数据看板聚合（仅管理员）
    近 7 日问答量按天聚合 + 最新动态（纪要完成 / 提问 / 注册 / 反馈合并取最新）+ 7 日增量。
    """

    permission_classes = [IsAuthenticated]
    EVENT_PER_KIND = 20  # 每类各取最新若干，合并后再取全局最新，防某类刷屏挤掉其他类

    def get(self, request):
        if not request.user.is_admin_role:
            return Response({'detail': '仅管理员可查看平台看板数据。'}, status=403)

        today = timezone.localdate()
        days = [today - timedelta(offset) for offset in range(6, -1, -1)]
        week_start = days[0]
        weekly = [
            {'day': d.strftime('%m-%d'), 'count': QaRecord.objects.filter(created_at__date=d).count()}
            for d in days
        ]

        events: list[tuple] = []
        for n in Note.objects.filter(status=Note.Status.DONE).select_related('uploader')[:self.EVENT_PER_KIND]:
            events.append((n.updated_at, f'「{n.title}」纪要生成完成（上传者：{n.uploader.username}）'))
        for r in QaRecord.objects.select_related('user', 'note')[:self.EVENT_PER_KIND]:
            if r.note_id:
                events.append((r.created_at, f'用户「{r.user.username}」围绕「{r.note.title}」提问'))
            else:
                events.append((r.created_at, f'用户「{r.user.username}」发起自由问答'))
        for u in User.objects.order_by('-created_at')[:self.EVENT_PER_KIND]:
            events.append((u.created_at, f'新用户「{u.username}」完成注册'))
        for f in Feedback.objects.select_related('user')[:self.EVENT_PER_KIND]:
            events.append((f.created_at, f'用户「{f.user.username}」提交反馈：{f.title}'))
        events.sort(key=lambda e: e[0], reverse=True)

        def fmt(dt) -> str:
            return timezone.localtime(dt).strftime('%m-%d %H:%M')

        return Response({
            'weekly': weekly,
            'events': [{'time': fmt(dt), 'text': text} for dt, text in events[:12]],
            'counts': {
                'users_7d': User.objects.filter(role='user', created_at__date__gte=week_start).count(),
                'notes_7d': Note.objects.filter(
                    status=Note.Status.DONE, updated_at__date__gte=week_start,
                ).count(),
                'qa_7d': sum(w['count'] for w in weekly),
            },
        })
