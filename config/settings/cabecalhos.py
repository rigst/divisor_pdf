"""Cabeçalhos de segurança aplicados por core/security_headers.py.

Separado de base.py para a política ficar num lugar só, com o motivo de cada
diretiva ao lado. base.py importa estes nomes, e development/production os
herdam de lá.
"""

import os

from dotenv import load_dotenv

# base.py importa este módulo antes do próprio load_dotenv(). Carregar aqui
# garante que um override no .env vale também para estes cabeçalhos; a chamada
# é idempotente e não sobrescreve o que já está no ambiente.
load_dotenv()


# APIs do navegador que o app não usa. Mesmo valor da frota (o dracma manda via
# nginx). O divisor não grava áudio nem vídeo: nenhum getUserMedia/MediaRecorder
# nos JS, então câmera e microfone ficam desligados também para o próprio site.
PERMISSIONS_POLICY = os.getenv(
    "PERMISSIONS_POLICY",
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
)

# Ligada em todos os ambientes, inclusive dev e testes: assim o e2e roda as
# telas sob a mesma política de produção e acusa o que ela bloquear.
ENABLE_CSP = os.getenv("ENABLE_CSP", "True").lower() in ("true", "1", "yes")

# Política do app público. Tudo o que o front carrega é do próprio domínio:
# fontes auto-hospedadas, CSS, JS e a imagem do topo. `data:` em img-src cobre
# os SVG embutidos no CSS (ruído de fundo, seta do select, check). O único
# script inline é o do tema no base.html, que leva o nonce. `style-src
# 'unsafe-inline'` porque os templates usam `style="display: none"` e o JS
# mexe em `.style`. O upload (XHR), o status (fetch) e o download (link para
# /api/download/) são same-origin: connect-src cai no default-src 'self'.
# Não há Worker, blob: nem iframe; worker-src/frame-src herdam o 'self'.
CONTENT_SECURITY_POLICY = os.getenv(
    "CONTENT_SECURITY_POLICY",
    "default-src 'self'; img-src 'self' data:; script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
)

ADMIN_PATH_PREFIX = "/admin/"

# Política exclusiva do admin. O 'unsafe-eval' é exigido pelo Alpine.js que o
# django-unfold usa: ele compila as expressões de `x-data`/`x-init` com
# `new Function()`. Sem isso o painel carrega sem menu, abas nem tema. A
# concessão fica presa ao /admin/, que só `is_staff` alcança.
CONTENT_SECURITY_POLICY_ADMIN = os.getenv(
    "CONTENT_SECURITY_POLICY_ADMIN",
    "default-src 'self'; img-src 'self' data:; "
    "script-src 'self' 'unsafe-eval' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
)
