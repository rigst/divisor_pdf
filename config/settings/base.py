"""
Configurações base do Django para o projeto Divisor de PDFs.
Compartilhadas entre development e production.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Carrega variáveis de ambiente do .env
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-dev-key-change-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]


# Application definition

INSTALLED_APPS = [
    # O unfold precisa vir antes do admin: é assim que os templates dele
    # sobrescrevem os do django.contrib.admin.
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Apps do projeto
    "splitter",
    "legal",
]

# O AceiteObrigatorioMiddleware do app `legal` NÃO entra aqui: ele existe para
# forçar o re-aceite de usuários autenticados, e este projeto não tem contas.
# O aceite é anônimo, validado no próprio upload (splitter/views.py).
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

UNFOLD = {
    "SITE_TITLE": "Divisor de PDFs",
    "SITE_HEADER": "Divisor de PDFs",
    "SITE_SUBHEADER": "Administração",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {
        # Violeta do tema do app (ds-theme-violet), para o admin não parecer
        # o painel de outro sistema.
        "primary": {
            "50": "245 243 255",
            "100": "237 233 254",
            "200": "221 214 254",
            "300": "196 181 253",
            "400": "167 139 250",
            "500": "139 92 246",
            "600": "124 58 237",
            "700": "109 40 217",
            "800": "91 33 182",
            "900": "76 29 149",
            "950": "46 16 101",
        },
    },
}

# Destino após o aceite nas telas do app `legal`; aqui não há dashboard.
LEGAL_REDIRECT_URL = "/"

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media files (uploads)
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# Default primary key field type

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ==============================================================================
# Upload & PDF Splitting Configuration
# ==============================================================================

# Tamanho máximo por arquivo de upload (em bytes)
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "500"))
MAX_UPLOAD_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024

# Tamanho máximo total de uploads por sessão (em bytes)
MAX_TOTAL_UPLOAD_MB = int(os.getenv("MAX_TOTAL_UPLOAD_MB", "2048"))
MAX_TOTAL_UPLOAD_SIZE = MAX_TOTAL_UPLOAD_MB * 1024 * 1024

# Django upload limits
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB em memória, resto vai para disco
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# Diretório para uploads temporários. Configurável porque o Django exige que
# ele exista — `check` falha com files.E001 se não existir — e nem todo
# ambiente provisiona media/tmp dentro do código: em CI o diretório não existe,
# e em produção pode ficar em outro volume.
FILE_UPLOAD_TEMP_DIR = os.getenv("FILE_UPLOAD_TEMP_DIR", str(BASE_DIR / "media" / "tmp"))

# Tempo máximo para cada chamada do Ghostscript
GHOSTSCRIPT_TIMEOUT_SECONDS = int(os.getenv("GHOSTSCRIPT_TIMEOUT_SECONDS", "300"))

# Limite de divisão informado pelo usuário usa MB decimal, igual ao exibido por
# gerenciadores de arquivo comuns: 2 MB = 2.000.000 bytes.
PDF_SPLIT_BYTES_PER_MB = 1000 * 1000


# ==============================================================================
# Session Configuration
# ==============================================================================

SESSION_COOKIE_AGE = int(os.getenv("SESSION_EXPIRY_SECONDS", "3600"))  # 1 hora
SESSION_SAVE_EVERY_REQUEST = True  # Renova a sessão a cada request


# ==============================================================================
# Celery Configuration
# ==============================================================================

CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

# Celery Beat schedule para limpeza automática
CELERY_BEAT_SCHEDULE = {
    "cleanup-expired-sessions": {
        "task": "splitter.tasks.cleanup_expired_sessions",
        "schedule": int(os.getenv("CLEANUP_INTERVAL_MINUTES", "15")) * 60,
    },
}


# ==============================================================================
# Cleanup Configuration
# ==============================================================================

# Tempo máximo de retenção dos arquivos processados (em segundos)
FILE_RETENTION_SECONDS = int(os.getenv("SESSION_EXPIRY_SECONDS", "3600"))
