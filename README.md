# Divisor PDF

Aplicacao Django para enviar PDFs, comprimir com Ghostscript, dividir em partes menores com `pypdf` e baixar o resultado como PDF unico ou ZIP. O processamento pesado roda em Celery.

## Requisitos

- Python 3.12+
- Ghostscript (`gs`)
- Redis para Celery em producao
- PostgreSQL em producao

No Ubuntu/Debian:

```bash
sudo apt update
sudo apt install ghostscript redis-server postgresql
```

## Ambiente local

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py runserver
```

Por padrao, `manage.py` usa `config.settings.development`, SQLite e Celery em modo eager. Isso permite testar a aplicacao sem Redis.

## Configuracao

Crie um arquivo `.env` conforme necessario:

```env
SECRET_KEY=troque-esta-chave
DEBUG=False
ALLOWED_HOSTS=seudominio.com,127.0.0.1
REDIS_URL=redis://localhost:6379/0
DB_NAME=divisor_pdf
DB_USER=divisor_pdf
DB_PASSWORD=senha-segura
DB_HOST=localhost
DB_PORT=5432
MAX_UPLOAD_SIZE_MB=500
MAX_TOTAL_UPLOAD_MB=2048
SESSION_EXPIRY_SECONDS=3600
CLEANUP_INTERVAL_MINUTES=15
GHOSTSCRIPT_TIMEOUT_SECONDS=300
DJANGO_SETTINGS_MODULE=config.settings.production
```

## Testes

```bash
./venv/bin/python manage.py check
./venv/bin/python manage.py test splitter
```

## Producao

Para o passo a passo completo (pacotes, PostgreSQL, systemd, Nginx, HTTPS com
Let's Encrypt e notas de SELinux no Fedora), veja **[DEPLOY.md](DEPLOY.md)**.

Os arquivos em `deploy/` trazem exemplos de Gunicorn, Nginx e systemd. Antes de usar:

- Ajuste caminhos absolutos para o servidor.
- Defina `DJANGO_SETTINGS_MODULE=config.settings.production` no `.env`.
- Defina `SECRET_KEY`, `ALLOWED_HOSTS`, `DB_NAME`, `DB_USER` e `DB_PASSWORD`; em producao, a aplicacao falha ao iniciar sem esses valores.
- Ajuste `client_max_body_size` no Nginx para acompanhar `MAX_TOTAL_UPLOAD_MB`.
- Rode `collectstatic` e `migrate`.
- Nao exponha `/media/` diretamente pelo Nginx; downloads devem passar pela view Django para validar sessao e status do job.

### Deploy automatizado

O script `deploy/deploy.sh` executa todos os passos de forma idempotente
(pull, dependencias, criacao de diretorios de runtime, `migrate`,
`collectstatic`, `check --deploy` e restart dos servicos):

```bash
./deploy/deploy.sh            # deploy completo
./deploy/deploy.sh --no-pull  # sem git pull (deploy local)
```

Comandos manuais equivalentes:

```bash
./venv/bin/python manage.py migrate --settings=config.settings.production
./venv/bin/python manage.py collectstatic --noinput --settings=config.settings.production
sudo systemctl restart divisor_pdf
sudo systemctl restart divisor_celery
```
