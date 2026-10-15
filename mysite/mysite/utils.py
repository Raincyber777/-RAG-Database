"""
通用工具：DRF 分页与角色权限类（所有业务 app 共用）
"""
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission


class StandardPagination(PageNumberPagination):
    """页码分页：?page=1&page_size=10（前端反馈页支持 10/20/30，上限 50）"""

    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50


class IsAdminRole(BasePermission):
    """管理员权限：role ∈ {admin, super} 才可访问（账号管理/内容巡检等）"""

    message = '需要管理员权限'

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in ('admin', 'super'))


class IsSuperRole(BasePermission):
    """超级管理员权限：新建 / 停用管理员账号等操作"""

    message = '需要超级管理员权限'

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role == 'super')


def validate_image(file):
    """反馈截图校验：仅图片格式，不超过 10MB（与用户端约定一致）"""
    ALLOWED_EXT = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
    MAX_SIZE = 10 * 1024 * 1024
    name = (file.name or '').lower()
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    if ext not in ALLOWED_EXT:
        raise ValidationError('仅支持图片格式（jpg / jpeg / png / webp / gif）')
    if file.size > MAX_SIZE:
        raise ValidationError('图片大小不能超过 10MB')
