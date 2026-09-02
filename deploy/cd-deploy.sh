#!/usr/bin/env bash
set -euo pipefail

# Disparado via SSH pelo usuário "deploy" (authorized_keys com command=
# forçado — ver rigst/ci RUNBOOK.md seção 7). Roda inteiro como "deploy";
# só o reload/restart no fim precisa de sudo (sudoers próprio de "deploy",
# nunca o de "rod").

APP_DIR=/var/www/divisor_pdf
FETCH_URL=https://github.com/rigst/divisor_pdf.git   # HTTPS anônimo — repo público, sem credencial
VENV=/var/www/divisor_pdf/venv
WEB_SERVICE=divisor_pdf.service   # reload (SIGHUP): zero downtime, socket nunca cai
OTHER_SERVICES=(divisor_celery.service)
HEALTH_URL="https://divisor.stolben.com/"   # sem /healthz/ neste app; home é pública (200 direto)
HEALTH_HEADER=""
BACKUP_SCRIPT=/var/www/divisor_pdf/deploy/backup_postgres.sh
# config/settings/base.py já chama load_dotenv() sozinho a partir do cwd —
# não dar source no .env em bash (ver RUNBOOK 7.4/7.7: python-dotenv aceita
# sintaxe que quebra o bash silenciosamente, achado em sistema_trilhas). O
# único motivo real pra exportar algo é DJANGO_SETTINGS_MODULE, que o
# systemd só injeta no processo do gunicorn, nunca em comandos ad-hoc via SSH.
EXTRA_ENV="DJANGO_SETTINGS_MODULE=config.settings.production"
LOCK_FILE=/tmp/divisor_pdf_cd_deploy.lock

main() {
  local sha
  sha="$(printf '%s' "${SSH_ORIGINAL_COMMAND:-}" | awk '{print $2}')"
  [[ "$sha" =~ ^[0-9a-f]{7,40}$ ]] || { echo "SHA inválido: '$sha'"; exit 1; }

  cd "$APP_DIR"
  git fetch "$FETCH_URL" main
  git merge-base --is-ancestor "$sha" FETCH_HEAD \
    || { echo "SHA não é ancestral do main remoto: $sha"; exit 1; }

  local antes; antes="$(git rev-parse HEAD)"

  local tem_migracao tem_requirements troca_gunicorn=0
  tem_migracao="$(git diff --name-only "HEAD..$sha" -- '*/migrations/*')"
  tem_requirements="$(git diff --name-only "HEAD..$sha" -- requirements.txt)"

  # SIGHUP recicla os workers mas não reexecuta o mestre: um upgrade de
  # gunicorn é instalado no venv e nunca entra em vigor. Só nesse caso vale a
  # janela de 502 do restart — ver rigst/ci RUNBOOK.md seção 7.1.2.
  if git diff "HEAD..$sha" -- requirements.txt requirements.lock \
     | grep -qiE '^[+-]gunicorn([[:space:]]|[=<>!~]|$)'; then
    troca_gunicorn=1
  fi

  if [[ -n "$tem_migracao" && -n "$BACKUP_SCRIPT" ]]; then
    "$BACKUP_SCRIPT"
  fi

  git merge --ff-only "$sha"

  if [[ -n "$tem_requirements" ]]; then
    "$VENV/bin/pip" install -r requirements.txt
  fi

  eval "export $EXTRA_ENV"

  "$VENV/bin/python" manage.py check --deploy --fail-level ERROR
  "$VENV/bin/python" manage.py migrate --check || "$VENV/bin/python" manage.py migrate
  "$VENV/bin/python" manage.py collectstatic --noinput

  if (( troca_gunicorn )); then
    sudo systemctl restart "$WEB_SERVICE"
  else
    sudo systemctl reload "$WEB_SERVICE"
  fi
  for unidade in "${OTHER_SERVICES[@]}"; do
    sudo systemctl restart "$unidade"
  done

  if [[ -n "$HEALTH_URL" ]]; then
    local codigo
    for _ in 1 2 3 4 5; do
      codigo="$(curl -s -o /dev/null -w '%{http_code}' ${HEALTH_HEADER:+-H "$HEALTH_HEADER"} "$HEALTH_URL")"
      [[ "$codigo" =~ ^[23][0-9][0-9]$ ]] && break
      sleep 2
    done
    if [[ ! "$codigo" =~ ^[23][0-9][0-9]$ ]]; then
      echo "Smoke-test falhou ($codigo). Rollback manual: git -C $APP_DIR reset --hard $antes"
      exit 1
    fi
  fi

  echo "Deploy de $sha concluído (era $antes)."
}

(
  flock -n 9 || { echo "Deploy já em andamento, saindo."; exit 1; }
  main "$@"
) 9>"$LOCK_FILE"
