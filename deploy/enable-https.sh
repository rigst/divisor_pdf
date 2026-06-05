#!/usr/bin/env bash
#
# Emite o certificado Let's Encrypt e ativa o HTTPS do DivisorPDF.
# Pré-requisitos: DNS de divisor.stolben.com apontando para este servidor (OK),
# vhost HTTP temporário já instalado (root-setup.sh) e porta 80 aberta.
# Rodar como root:  sudo bash /var/www/divisor_pdf/deploy/enable-https.sh
#
set -euo pipefail
P=/var/www/divisor_pdf
DOMINIO=divisor.stolben.com
EMAIL=rodrigo.stolben@gmail.com

echo "==> [1/3] Emitindo certificado (webroot)"
certbot certonly --webroot -w /var/www/certbot -d "$DOMINIO" \
    --agree-tos -m "$EMAIL" --non-interactive

echo "==> [2/3] Ativando vhost com HTTPS (bloco 443)"
cp "$P/deploy/nginx.conf" /etc/nginx/sites-available/divisor_pdf
nginx -t
systemctl reload nginx

echo "==> [3/3] Testes"
sleep 1
echo "--- HTTP (espera 301 -> https) ---"
curl -sS -o /dev/null -w "HTTP %{http_code} -> %{redirect_url}\n" "http://$DOMINIO/" || true
echo "--- HTTPS (espera 200) ---"
curl -sS -o /dev/null -w "HTTP %{http_code}\n" "https://$DOMINIO/" || true
echo "--- Renovação automática ---"
certbot renew --dry-run >/dev/null 2>&1 && echo "renovação OK" || echo "checar certbot renew"

echo "==> HTTPS ativo."
