from django.apps import AppConfig


class SafetyConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'safety'
    verbose_name = '内容安全'
