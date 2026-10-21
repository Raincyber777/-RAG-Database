from .agent_test_settings import *

INSTALLED_APPS = [*INSTALLED_APPS, 'feedback']
MEDIA_URL = '/media/'
ROOT_URLCONF = 'mysite.oss_test_urls'
ALLOWED_HOSTS = ['testserver']
