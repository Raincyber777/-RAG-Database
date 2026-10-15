"""
URL configuration for mysite project.
智学纪要 · RAG 知识问答系统 后端路由
/api/auth/*      认证（注册 / 登录 / 登出 / 资料 / 密码）
/api/notes/*     课堂纪要        /api/admin/notes/    纪要巡检
/api/qa/*        智能问答        /api/admin/qa/       问答巡检
/api/quiz/*      题库与错题本    /api/admin/quiz/     题库巡检
/api/feedback/   问题反馈        /api/admin/feedbacks/ 反馈处理
/api/faqs/       常见问题        /api/admin/faqs/     FAQ 维护
/api/accounts/*  （管理端）账号管理 /api/stats/summary/ 数据速览
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from .views import StatsSummaryView

urlpatterns = [
    path('admin/dj/', admin.site.urls),  # Django 后台（调试用；业务管理端走 /api/admin/*）

    path('api/', include('accounts.urls')),
    path('api/', include('notes.urls')),
    path('api/', include('qa.urls')),
    path('api/', include('quiz.urls')),
    path('api/', include('feedback.urls')),
    path('api/', include('faq.urls')),
    path('api/stats/summary/', StatsSummaryView.as_view()),
]

# 开发环境：提供用户上传的媒体文件（头像 / 材料 / 截图）
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
