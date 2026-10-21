"""Isolated integration tests: no local secrets, production DB or external services."""
SECRET_KEY = 'offline-agent-integration-test-only'
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'rest_framework',
                  'accounts', 'notes', 'qa']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
AUTH_USER_MODEL = 'accounts.User'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
ROOT_URLCONF = 'qa.urls'
USE_TZ = True
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
REST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES': []}
DASHSCOPE_API_KEY = 'offline-only'
AGENT_ENABLED = True
AGENT_TENANT_ID = 'classroom'
AGENT_ENABLE_SEARCH = False
