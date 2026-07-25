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

Para o passo a passo completo no **Ubuntu Server** (pacotes, PostgreSQL,
systemd, Nginx, HTTPS com Let's Encrypt e firewall), veja **[DEPLOY.md](DEPLOY.md)**.

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

## Conformidade legal (LGPD / Marco Civil)

O app `legal` versiona os Termos de Uso e a Política de Privacidade e registra o aceite.
Como aqui não há cadastro, o aceite é **anônimo e preso à sessão**: pedido uma vez antes
do primeiro envio, com checkbox desmarcado e validação no servidor, e gravado com data,
hora, IP, navegador, identificador de sessão e o `sha256` do texto exato aceito.

Os registros de acesso do nginx são mantidos por **6 meses**, como exige o art. 15 do
Marco Civil (`deploy/logrotate/stolben-acesso` e `deploy/nginx_acesso.py`).

O procedimento completo está em [docs/CONFORMIDADE.md](docs/CONFORMIDADE.md).

```bash
./venv/bin/python manage.py importar_documentos_legais --publicar  # seed inicial
./venv/bin/python manage.py exportar_documentos_legais             # espelho em git
```

## Licença

Este projeto é distribuído sob a **GNU Affero General Public License v3.0** (ver [LICENSE](LICENSE)).

A compressão de PDFs usa o [Ghostscript](https://www.ghostscript.com/) (AGPL-3.0, Artifex Software), invocado como processo externo. O código-fonte completo deste sistema está disponível em <https://github.com/rigst/divisor_pdf>.

O inventário das bibliotecas de terceiros está em [docs/LICENCAS-TERCEIROS.md](docs/LICENCAS-TERCEIROS.md), regenerável com `./venv/bin/python scripts/licencas_terceiros.py`.
