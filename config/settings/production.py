"""
Configurações de produção.
Usa PostgreSQL, Redis para cache/sessões, e configurações de segurança.
"""

import os
from .base import *  # noqa: F401, F403

DEBUG = False


def _required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f'A variável de ambiente {name} é obrigatória em produção.')
    return value


SECRET_KEY = _required_env('SECRET_KEY')
if SECRET_KEY.startswith('django-insecure-'):
    raise RuntimeError('SECRET_KEY de produção não pode usar valor django-insecure.')

ALLOWED_HOSTS = [
    h.strip() for h in _required_env('ALLOWED_HOSTS').split(',') if h.strip()
]

# Database — PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': _required_env('DB_NAME'),
        'USER': _required_env('DB_USER'),
        'PASSWORD': _required_env('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': 600,
    }
}

# Cache — Redis
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.getenv('REDIS_URL', 'redis://localhost:6379/1'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}

# Sessão via Redis (mais rápido e escalável)
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'

# Segurança
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Static files
STATIC_ROOT = os.getenv('STATIC_ROOT', str(BASE_DIR / 'staticfiles'))

# Cache-busting: gera nomes com hash de conteúdo (ex.: style.<hash>.css) via
# manifesto. Como o Nginx serve /static/ com `expires 30d`, sem isto os
# visitantes que abriram o site antes de um redesign continuam recebendo o
# CSS/JS antigo em cache — fazendo a página abrir desformatada. Com o hash,
# qualquer mudança de conteúdo gera uma URL nova e o navegador busca o arquivo
# atualizado, mantendo o cache longo seguro.
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage',
    },
}
