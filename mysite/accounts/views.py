"""
账号接口：
- 统一登录 / 注册（含验证码）/ 登出 / 个人资料 / 修改密码（两端共用）
- 管理端账号管理：列表筛选 / 新建管理员 / 重置密码 / 停用恢复
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.validators import validate_email as django_validate_email
from django.db.models import Q
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from mysite.utils import IsAdminRole, IsSuperRole

from .models import VerificationCode
from .serializers import (
    AdminCreateSerializer, ChangeEmailSerializer, ChangePasswordSerializer,
    ChangePhoneSerializer, ProfileSerializer, RegisterSerializer,
    ResetPasswordSerializer, UserSerializer,
)
from .verification import (
    CODE_TTL_SECONDS, CodeSendError, ThrottledError, channel_ready, issue_code,
)
from .serializers import PHONE_RE

User = get_user_model()


def _token_payload(user: User) -> dict:
    """签发 JWT 并附带用户信息（前端按 role 分流：user→用户端，admin/super→管理端）"""
    refresh = RefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'user': UserSerializer(user).data,
    }


class RegisterView(APIView):
    """POST /api/auth/register/ —— 注册普通用户（需验证码，成功即登录，返回 token）"""

    permission_classes = [AllowAny]

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        return Response(_token_payload(user), status=status.HTTP_201_CREATED)


class CodeChannelsView(APIView):
    """GET /api/auth/code-channels/ —— 哪些验证码接收方式已配置可用（前端据此隐藏未开通项）"""

    permission_classes = [AllowAny]

    def get(self, request):
        # 键名与发码接口的 channel 取值对齐：email=邮箱，phone=短信（发到手机号）
        return Response({
            'email': channel_ready(VerificationCode.Channel.EMAIL),
            'phone': channel_ready(VerificationCode.Channel.PHONE),
        })


class CodeSendView(APIView):
    """
    POST /api/auth/code/send/ —— 发送验证码
    purpose=register（默认）：channel=email|phone + target=目标，目标不能已被注册
    purpose=change_phone|change_email：仅限已登录，验证码固定发往账号绑定邮箱
    """

    permission_classes = [AllowAny]

    def post(self, request):
        purpose = (request.data.get('purpose') or 'register').strip()
        if purpose not in (VerificationCode.Purpose.REGISTER,
                           VerificationCode.Purpose.CHANGE_PHONE,
                           VerificationCode.Purpose.CHANGE_EMAIL):
            return Response({'detail': '用途不合法'}, status=status.HTTP_400_BAD_REQUEST)

        if purpose == VerificationCode.Purpose.REGISTER:
            channel = (request.data.get('channel') or '').strip()
            target = (request.data.get('target') or '').strip()
            if channel not in (VerificationCode.Channel.EMAIL, VerificationCode.Channel.PHONE):
                return Response({'detail': '接收方式不合法'}, status=status.HTTP_400_BAD_REQUEST)
            if channel == VerificationCode.Channel.EMAIL:
                try:
                    django_validate_email(target)
                except Exception:
                    return Response({'detail': '邮箱格式不正确'}, status=status.HTTP_400_BAD_REQUEST)
                target = target.lower()
                exists_tip = '该邮箱已注册，请直接登录'
                user_filter = Q(email=target)
            else:
                if not PHONE_RE.match(target):
                    return Response({'detail': '手机号格式不正确'}, status=status.HTTP_400_BAD_REQUEST)
                exists_tip = '该手机号已注册，请直接登录'
                user_filter = Q(phone=target)
            # 已注册的目标不再发码，避免用户在注册页打转
            if User.objects.filter(user_filter).exists():
                return Response({'detail': exists_tip}, status=status.HTTP_400_BAD_REQUEST)
        else:
            # 换绑场景：发往当前登录账号的绑定邮箱（JWT 照常解析，permission 虽为 AllowAny）
            if not request.user.is_authenticated:
                return Response({'detail': '请先登录'}, status=status.HTTP_401_UNAUTHORIZED)
            channel = VerificationCode.Channel.EMAIL
            target = (request.user.email or '').strip().lower()
            if not target:
                return Response({'detail': '当前账号未绑定邮箱，无法验证身份'}, status=status.HTTP_400_BAD_REQUEST)

        # 服务器未配置该通道：明确报错；本地 DEBUG 则退化为控制台出码，方便联调
        deliver = True
        if not channel_ready(channel):
            if not settings.DEBUG:
                return Response({'detail': '验证码服务未配置，请联系管理员'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            deliver = False
        try:
            code = issue_code(channel, target, purpose=purpose, deliver=deliver)
        except ThrottledError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except CodeSendError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        data = {'detail': f'验证码已发送至{"邮箱" if channel == "email" else "手机号"}，请查收',
                'expires_in': CODE_TTL_SECONDS}
        if not deliver:  # 调试模式：验证码未真正发出，直接返回供本地自动化 / 联调使用
            data['dev_code'] = code
        return Response(data)


class ChangePhoneView(APIView):
    """POST /api/auth/me/phone/ —— 更换手机号（绑定邮箱收码验证身份后改绑）"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = ChangePhoneSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        request.user.phone = ser.validated_data['phone']
        request.user.save(update_fields=['phone'])
        return Response(UserSerializer(request.user).data)


class ChangeEmailView(APIView):
    """POST /api/auth/me/email/ —— 更换邮箱（原绑定邮箱收码验证身份后改绑）"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = ChangeEmailSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        request.user.email = ser.validated_data['email']
        request.user.save(update_fields=['email'])
        return Response(UserSerializer(request.user).data)


class LoginView(APIView):
    """POST /api/auth/login/ —— 统一登录（账号可为 邮箱 / 手机号 / 用户名）"""

    permission_classes = [AllowAny]

    def post(self, request):
        account = (request.data.get('account') or request.data.get('email')
                   or request.data.get('phone') or '').strip()
        password = request.data.get('password') or ''
        if not account or not password:
            return Response({'detail': '请输入账号和密码'}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(
            Q(email=account.lower()) | Q(phone=account) | Q(username=account),
        ).first()
        # 细分失败原因给前端展示；注意不要用 401——统一封装会把 401 当 token 过期处理
        if user is None:
            return Response({'detail': '该账号尚未注册，请先注册'}, status=status.HTTP_400_BAD_REQUEST)
        if not user.check_password(password):
            return Response({'detail': '密码错误，请重新输入'}, status=status.HTTP_400_BAD_REQUEST)
        if user.status == User.Status.DISABLED:
            return Response({'detail': '该账号已被停用，请联系管理员'}, status=status.HTTP_403_FORBIDDEN)
        return Response(_token_payload(user))


class LogoutView(APIView):
    """POST /api/auth/logout/ —— 将 refresh token 拉黑（前端同时清除本地 token）"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            RefreshToken(request.data.get('refresh')).blacklist()
        except (TokenError, KeyError, AttributeError):
            pass  # token 缺失或已失效视为已登出
        return Response({'detail': '已退出登录'})


class MeView(APIView):
    """GET /api/auth/me/ —— 当前登录账号信息；PATCH —— 修改个人资料"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        ser = ProfileSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        # 对外字段 name 对应模型的 username（模型无 name 字段，直接 setattr 会静默丢失）
        for field, value in ser.validated_data.items():
            setattr(request.user, 'username' if field == 'name' else field, value)
        request.user.save()
        return Response(UserSerializer(request.user).data)


class ChangePasswordView(APIView):
    """POST /api/auth/password/ —— 修改密码（验证原密码）"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = ChangePasswordSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        request.user.set_password(ser.validated_data['new_password'])
        request.user.save()
        return Response({'detail': '密码修改成功'})


# ---------- 管理端：账号管理 ----------

class AdminAccountListView(ListAPIView):
    """
    GET /api/admin/accounts/ —— 账号列表（用户与管理员统一管理）
    ?role=user|admin（admin 含超管）&status=active|disabled&search=用户名/手机号/邮箱
    默认新注册的在前；「管理员在前」的分组排序由前端完成
    """

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = User.objects.all().order_by('-created_at')
        params = self.request.query_params
        role = params.get('role')
        if role == 'user':
            qs = qs.filter(role=User.Role.USER)
        elif role == 'admin':
            qs = qs.exclude(role=User.Role.USER)
        stat = params.get('status')
        if stat in (User.Status.ACTIVE, User.Status.DISABLED):
            qs = qs.filter(status=stat)
        keyword = (params.get('search') or '').strip()
        if keyword:
            qs = qs.filter(
                Q(username__icontains=keyword) | Q(phone__icontains=keyword) | Q(email__icontains=keyword),
            )
        return qs


class AdminAccountCreateView(APIView):
    """POST /api/admin/accounts/ —— 新建管理员账号（仅超级管理员）"""

    permission_classes = [IsAuthenticated, IsAdminRole, IsSuperRole]

    def post(self, request):
        ser = AdminCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class AdminAccountResetPasswordView(APIView):
    """POST /api/admin/accounts/{id}/reset_password/ —— 重置密码（至少 8 位）"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk):
        target = User.objects.filter(pk=pk).first()
        if target is None:
            return Response({'detail': '账号不存在'}, status=status.HTTP_404_NOT_FOUND)
        ser = ResetPasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        target.set_password(ser.validated_data['password'])
        target.save()
        return Response({'detail': f'已重置「{target.username}」的密码'})


class AdminAccountToggleStatusView(APIView):
    """POST /api/admin/accounts/{id}/toggle_status/ —— 停用 / 恢复账号"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk):
        target = User.objects.filter(pk=pk).first()
        if target is None:
            return Response({'detail': '账号不存在'}, status=status.HTTP_404_NOT_FOUND)
        if target.role == User.Role.SUPER:
            return Response({'detail': '超级管理员账号不可停用'}, status=status.HTTP_400_BAD_REQUEST)
        if target.pk == request.user.pk:
            return Response({'detail': '不能停用当前登录的账号'}, status=status.HTTP_400_BAD_REQUEST)
        target.status = (
            User.Status.DISABLED if target.status == User.Status.ACTIVE else User.Status.ACTIVE
        )
        target.save()
        return Response(UserSerializer(target).data)
