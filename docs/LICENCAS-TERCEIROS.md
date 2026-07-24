# Licenças de terceiros — Divisor de PDFs

Gerado por `scripts/licencas_terceiros.py` em 2026-07-24 a partir dos pacotes instalados no venv de produção.
Para regenerar: `./venv/bin/python scripts/licencas_terceiros.py`.

O código deste projeto é licenciado sob **AGPL-3.0** (ver `LICENSE`). As bibliotecas abaixo permanecem sob suas licenças originais.

## Dependências diretas

| Pacote | Versão | Licença |
|---|---|---|
| celery | 5.6.3 | BSD-3-Clause |
| Django | 6.0.6 | BSD-3-Clause |
| django-redis | 6.0.0 | BSD License |
| gunicorn | 26.0.0 | MIT |
| psycopg2-binary | 2.9.12 | GNU Library or Lesser General Public License (LGPL) |
| pypdf | 6.12.2 | BSD-3-Clause |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| redis | 8.0.0 | MIT |

## Dependências transitivas

| Pacote | Versão | Licença |
|---|---|---|
| amqp | 5.3.1 | BSD License |
| asgiref | 3.11.1 | BSD License |
| billiard | 4.2.4 | BSD License |
| click | 8.4.1 | BSD-3-Clause |
| click-didyoumean | 0.3.1 | MIT License |
| click-plugins | 1.1.1.2 | BSD License |
| click-repl | 0.3.0 | MIT |
| kombu | 5.6.2 | BSD-3-Clause |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| prompt_toolkit | 3.0.52 | BSD License |
| python-dateutil | 2.9.0.post0 | BSD License / Apache Software License |
| six | 1.17.0 | MIT License |
| sqlparse | 0.5.5 | BSD License |
| tzdata | 2026.2 | Apache-2.0 |
| tzlocal | 5.3.1 | MIT License |
| vine | 5.1.0 | BSD License |
| wcwidth | 0.7.0 | MIT |

## Programas externos

Invocados por `subprocess` como processos separados — não são linkados ao código deste projeto.

| Programa | Versão | Licença | Observação |
|---|---|---|---|
| Ghostscript | 10.02.1 | AGPL-3.0 | Compressão de PDF (`gs`), chamado em `splitter/services.py` |

## Componentes com licença recíproca (copyleft)

Listados para conferência ao redistribuir o código ou ao combinar com componentes fechados. O uso como biblioteca, sem modificação e sem distribuição do binário, não propaga obrigações de abertura.

| Pacote | Versão | Licença |
|---|---|---|
| psycopg2-binary | 2.9.12 | GNU Library or Lesser General Public License (LGPL) |

## Notas de manutenção

- **Redis**: o servidor em uso é a série 7.0 (BSD-3-Clause). As versões 7.4 a 7.9 passaram a ser RSALv2/SSPL, que não são licenças livres segundo a OSI. Ao atualizar o servidor, reveja esta seção e a página de licenças do site.
- Os programas externos acima rodam como processos separados, invocados por linha de comando. Não há linkagem com o código deste projeto, e o serviço não distribui os binários — por isso as obrigações de reciprocidade da GPL não se estendem a este código.
