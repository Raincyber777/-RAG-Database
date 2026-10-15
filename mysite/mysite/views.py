"""
平台 / 个人数据速览接口（工作台与数据看板用），按角色返回不同口径：
- 普通用户：自己的纪要 / 问答 / 练习 / 错题数量
- 管理员·超管：平台用户数 / 纪要数 / 问答数 / 待处理反馈数
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
