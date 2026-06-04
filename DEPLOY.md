# Guia de Deploy — DivisorPDF

Passo a passo para colocar a aplicação em produção com **Gunicorn + Nginx +
Celery + PostgreSQL + Redis**, com HTTPS via Let's Encrypt.

> Substitua `SEU_DOMINIO` pelo domínio real em todos os comandos/arquivos.
> Os arquivos de exemplo em `deploy/` assumem o caminho
> `/home/rodrigostolben/Projetos/divisor_pdf`. Ajuste se o seu for diferente.

---

## 1. Pacotes do sistema

**Fedora / RHEL:**
```bash
sudo dnf install -y ghostscript redis postgresql-server nginx certbot python3-certbot-nginx python3 git
sudo postgresql-setup --initdb            # só na primeira vez
sudo systemctl enable --now redis postgresql nginx
```

**Debian / Ubuntu:**
```bash
sudo apt update
sudo apt install -y ghostscript redis-server postgresql nginx certbot python3-certbot-nginx python3-venv git
sudo systemctl enable --now redis-server postgresql nginx
```

Confira o Ghostscript (a compressão depende dele):
```bash
gs --version
```

---

## 2. Banco de dados PostgreSQL

```bash
sudo -u postgres psql <<'SQL'
CREATE USER divisor_pdf WITH PASSWORD 'TROQUE_ESTA_SENHA';
CREATE DATABASE divisor_pdf OWNER divisor_pdf;
ALTER ROLE divisor_pdf SET client_encoding TO 'utf8';
ALTER ROLE divisor_pdf SET timezone TO 'America/Sao_Paulo';
SQL
```

---

## 3. Código e ambiente virtual

```bash
cd /home/rodrigostolben/Projetos/divisor_pdf      # ou clone o repositório aqui
git pull --ff-only

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

---

## 4. Arquivo `.env`

```bash
cp .env.example .env
```

Edite o `.env` e preencha (no mínimo):

```env
DJANGO_SETTINGS_MODULE=config.settings.production
DEBUG=False
SECRET_KEY=<gere abaixo>
ALLOWED_HOSTS=SEU_DOMINIO

REDIS_URL=redis://localhost:6379/0

DB_NAME=divisor_pdf
DB_USER=divisor_pdf
DB_PASSWORD=TROQUE_ESTA_SENHA
DB_HOST=localhost
DB_PORT=5432

MAX_UPLOAD_SIZE_MB=500
MAX_TOTAL_UPLOAD_MB=2048
GHOSTSCRIPT_TIMEOUT_SECONDS=300
SESSION_EXPIRY_SECONDS=3600
CLEANUP_INTERVAL_MINUTES=15
```

Gere uma `SECRET_KEY` forte:
```bash
./venv/bin/python -c "import secrets; print(secrets.token_urlsafe(64))"
```

---

## 5. Migrações e arquivos estáticos

Use o script (faz tudo de forma idempotente):
```bash
./deploy/deploy.sh --no-pull
```

Ou manualmente:
```bash
mkdir -p media/tmp media/sessions staticfiles
./venv/bin/python manage.py migrate --noinput --settings=config.settings.production
./venv/bin/python manage.py collectstatic --noinput --settings=config.settings.production
./venv/bin/python manage.py createsuperuser --settings=config.settings.production   # opcional (admin)
```

---

## 6. Serviços systemd (Gunicorn + Celery)

Antes de copiar, ajuste o **grupo** do serviço para o usuário do nginx:
- **Debian/Ubuntu:** `Group=www-data` (já está assim nos arquivos)
- **Fedora/RHEL:** troque para `Group=nginx` em
  `deploy/systemd/divisor_pdf.service` e `deploy/systemd/divisor_celery.service`

Instale e inicie:
```bash
sudo cp deploy/systemd/divisor_pdf.service /etc/systemd/system/
sudo cp deploy/systemd/divisor_celery.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now divisor_pdf divisor_celery
sudo systemctl status divisor_pdf divisor_celery --no-pager
```

O Gunicorn cria o socket em `divisor_pdf.sock` dentro do projeto. Para o nginx
conseguir acessá-lo, o diretório `home` precisa ser "atravessável":
```bash
chmod o+x /home/rodrigostolben /home/rodrigostolben/Projetos /home/rodrigostolben/Projetos/divisor_pdf
```

---

## 7. Nginx (primeiro em HTTP, para emitir o certificado)

```bash
sudo mkdir -p /var/www/certbot
# Edite deploy/nginx.conf e troque SEU_DOMINIO pelo domínio real
sudo cp deploy/nginx.conf /etc/nginx/conf.d/divisor_pdf.conf
```

Para conseguir emitir o certificado, o bloco `server 443` ainda não funciona
(o cert não existe). **Comente temporariamente o bloco `server { listen 443 ... }`
inteiro** e deixe só o bloco da porta 80. Então:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 8. HTTPS com Let's Encrypt

Emita o certificado via webroot (o bloco porta 80 já serve o desafio):
```bash
sudo certbot certonly --webroot -w /var/www/certbot -d SEU_DOMINIO --agree-tos -m seu-email@exemplo.com
```

Depois de emitido, **descomente o bloco `server 443`** em
`/etc/nginx/conf.d/divisor_pdf.conf` e recarregue:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

Renovação automática (o certbot já instala um timer; teste com):
```bash
sudo certbot renew --dry-run
```

---

## 9. Fedora/RHEL: SELinux (pular no Debian/Ubuntu)

O SELinux do Fedora bloqueia o nginx de conectar no socket e de ler arquivos em
`/home` por padrão. Habilite:
```bash
# Permitir que o nginx faça proxy / conecte no socket do Gunicorn
sudo setsebool -P httpd_can_network_connect 1
# Permitir que o nginx leia conteúdo no /home do usuário
sudo setsebool -P httpd_read_user_content 1
```

Se o nginx ainda retornar 502/403, verifique as negações do SELinux:
```bash
sudo ausearch -m avc -ts recent
```
e ajuste o contexto dos estáticos, se necessário:
```bash
sudo chcon -Rt httpd_sys_content_t /home/rodrigostolben/Projetos/divisor_pdf/staticfiles
```

---

## 10. Verificação final

```bash
curl -I http://SEU_DOMINIO          # deve responder 301 -> https
curl -I https://SEU_DOMINIO         # deve responder 200
```

Checklist:
- [ ] `https://SEU_DOMINIO` abre a interface
- [ ] Upload de um PDF pequeno → divide/comprime → download funciona
- [ ] `sudo systemctl status divisor_pdf divisor_celery` ativos
- [ ] Logs sem erro: `journalctl -u divisor_pdf -u divisor_celery -n 50`

---

## 11. Atualizações futuras

A cada nova versão, basta:
```bash
cd /home/rodrigostolben/Projetos/divisor_pdf
./deploy/deploy.sh
```
(o script faz pull, instala deps, migra, coleta estáticos, valida e reinicia os serviços).

---

## 12. Troubleshooting rápido

| Sintoma | Causa provável | Ação |
|---|---|---|
| 502 Bad Gateway | Gunicorn fora / socket inacessível | `systemctl status divisor_pdf`; permissões do `home` (passo 6); SELinux (passo 9) |
| 403 nos estáticos | Permissão / contexto SELinux | passo 9 (`chcon`); confira `alias` no nginx |
| Compressão não reduz / falha | Ghostscript ausente | `gs --version`; reinstale o pacote |
| Upload trava ou erro 413 | `client_max_body_size` menor que o upload | alinhe com `MAX_TOTAL_UPLOAD_MB` no nginx |
| Job fica "processando" pra sempre | Celery parado / Redis fora | `systemctl status divisor_celery redis` |
| `RuntimeError: ... obrigatória em produção` | `.env` incompleto | preencha `SECRET_KEY`, `ALLOWED_HOSTS`, `DB_*` |
