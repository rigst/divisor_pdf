"""
Configurações de desenvolvimento.
Usa SQLite e sessões em banco de dados.
"""

from .base import *  # noqa: F401, F403

DEBUG = True

# Database — SQLite para desenvolvimento
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Sessão em banco de dados (mais simples para dev, sem Redis obrigatório)
SESSION_ENGINE = 'django.contrib.sessions.backends.db'

# Em desenvolvimento, Celery roda de forma eager (síncrona inline) para não depender de servidor Redis ativo
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
# Em modo eager, usa cache em memória para resultados (sem dependências externas)
CELERY_RESULT_BACKEND = 'cache'
CELERY_CACHE_BACKEND = 'memory'
