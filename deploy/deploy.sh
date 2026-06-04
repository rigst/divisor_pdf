#!/usr/bin/env bash
#
# Deploy do DivisorPDF em produção.
# Idempotente: pode ser executado a cada atualização.
#
# Uso:
#   ./deploy/deploy.sh            # deploy completo (pull + deps + migrate + static + restart)
#   ./deploy/deploy.sh --no-pull  # pula o git pull (deploy local)
#
# Pré-requisitos no servidor:
#   - .env preenchido (veja .env.example) com DJANGO_SETTINGS_MODULE=config.settings.production
#   - Ghostscript (gs), Redis e PostgreSQL instalados e ativos
#   - venv criado em ./venv
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON="$PROJECT_DIR/venv/bin/python"
PIP="$PROJECT_DIR/venv/bin/pip"
SETTINGS="config.settings.production"
DO_PULL=1

for arg in "$@"; do
    case "$arg" in
        --no-pull) DO_PULL=0 ;;
        *) echo "Argumento desconhecido: $arg" >&2; exit 2 ;;
    esac
done

echo "==> Diretório do projeto: $PROJECT_DIR"

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "ERRO: arquivo .env não encontrado. Copie .env.example para .env e preencha os valores." >&2
    exit 1
fi

if [ ! -x "$PYTHON" ]; then
    echo "ERRO: venv não encontrado em $PROJECT_DIR/venv. Crie com: python -m venv venv" >&2
    exit 1
fi

if [ "$DO_PULL" -eq 1 ]; then
    echo "==> Atualizando código (git pull)"
    git pull --ff-only
fi

echo "==> Instalando dependências"
"$PIP" install -q -r requirements.txt

echo "==> Garantindo diretórios de runtime"
mkdir -p "$PROJECT_DIR/media/tmp" "$PROJECT_DIR/media/sessions" "$PROJECT_DIR/staticfiles"

echo "==> Aplicando migrações"
"$PYTHON" manage.py migrate --noinput --settings="$SETTINGS"

echo "==> Coletando arquivos estáticos"
"$PYTHON" manage.py collectstatic --noinput --settings="$SETTINGS"

echo "==> Validando configuração de produção"
"$PYTHON" manage.py check --deploy --settings="$SETTINGS"

if command -v systemctl >/dev/null 2>&1; then
    echo "==> Reiniciando serviços"
    sudo systemctl restart divisor_pdf
    sudo systemctl restart divisor_celery
    echo "==> Status dos serviços"
    systemctl --no-pager --lines=0 status divisor_pdf divisor_celery || true
else
    echo "==> systemctl indisponível; reinicie os serviços manualmente."
fi

echo "==> Deploy concluído."
