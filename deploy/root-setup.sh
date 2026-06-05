#!/usr/bin/env bash
#
# Passos de root do deploy do DivisorPDF (systemd + nginx HTTP temporário).
# Idempotente. NÃO mexe no site 'default' nem nos outros vhosts.
# Rodar como root:  sudo bash /var/www/divisor_pdf/deploy/root-setup.sh
#
set -euo pipefail
P=/var/www/divisor_pdf

echo "==> [1/4] Instalando units do systemd"
cp "$P/deploy/systemd/divisor_pdf.service"    /etc/systemd/system/
cp "$P/deploy/systemd/divisor_celery.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now divisor_pdf divisor_celery

echo "==> [2/4] Diretório do desafio ACME"
mkdir -p /var/www/certbot

echo "==> [3/4] Instalando vhost nginx (HTTP temporário)"
cp "$P/deploy/nginx-http-temp.conf" /etc/nginx/sites-available/divisor_pdf
ln -sf /etc/nginx/sites-available/divisor_pdf /etc/nginx/sites-enabled/divisor_pdf
nginx -t
systemctl reload nginx

echo "==> [4/4] Status"
sleep 1
systemctl --no-pager --lines=0 status divisor_pdf divisor_celery || true
echo "--- socket ---"
ls -la "$P/divisor_pdf.sock" 2>&1 || echo "socket ainda não criado"
echo "--- teste local via nginx (porta 80) ---"
curl -sS -o /dev/null -w "HTTP %{http_code} -> %{redirect_url}\n" \
  -H "Host: divisor.stolben.com" http://127.0.0.1/ || true

echo "==> Concluído."
