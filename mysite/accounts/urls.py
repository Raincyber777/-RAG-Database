"""账号相关路由：/api/auth/* 与 /api/admin/accounts/*"""
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    # 统一认证（两端共用）
    path('auth/register/', views.RegisterView.as_view()),
    path('auth/login/', views.LoginView.as_view()),
    path('auth/refresh/', TokenRefreshView.as_view()),  # access 过期后用 refresh 换新
    path('auth/logout/', views.LogoutView.as_view()),
    path('auth/me/', views.MeView.as_view()),
    path('auth/password/', views.ChangePasswordView.as_view()),
    # 注册验证码：先查可用接收方式，再发码，最后带码注册
    path('auth/code-channels/', views.CodeChannelsView.as_view()),
    path('auth/code/send/', views.CodeSendView.as_view()),
    # 更换手机号 / 邮箱（绑定邮箱收码验证身份）
    path('auth/me/phone/', views.ChangePhoneView.as_view()),
    path('auth/me/email/', views.ChangeEmailView.as_view()),

    # 管理端账号管理
    path('admin/accounts/', views.AdminAccountListView.as_view()),
    path('admin/accounts/create/', views.AdminAccountCreateView.as_view()),
    path('admin/accounts/<int:pk>/reset_password/', views.AdminAccountResetPasswordView.as_view()),
    path('admin/accounts/<int:pk>/toggle_status/', views.AdminAccountToggleStatusView.as_view()),
]
