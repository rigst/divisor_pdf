"""
Configurações da suíte no CI: as de desenvolvimento, com PostgreSQL.

Herda tudo de development (Celery eager, sessão em banco, sem Redis) e troca
só o banco. Produção roda em PostgreSQL; rodar a suíte em SQLite deixava
passar diferença real de comportamento (tipos, ordenação, transações,
constraints), que só apareceria no servidor.

O CI aponta `test-settings-module` para cá e sobe um postgres:16 com
usuário/senha/banco `postgres` em localhost:5432; as variáveis DB_* vêm do
`django-env` do ci.yml. Localmente, sem Postgres à mão, o pytest.ini continua
em `config.settings.development` (SQLite) — a variável DJANGO_SETTINGS_MODULE
do CI tem precedência sobre o ini.
"""

import os

from .development import *

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "postgres"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}
