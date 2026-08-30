"""Constantes usadas só pela suíte de testes."""

import secrets

# Sorteada a cada processo de teste, e não escrita no código. Duas razões:
# nenhum literal de credencial fica versionado (o que os scanners acusam, com
# razão, porque um dia alguém copia o literal para um settings), e nenhum teste
# consegue depender do valor — se depender, quebra no processo seguinte.
SENHA_TESTE = secrets.token_urlsafe(16)

# Qualquer senha diferente de SENHA_TESTE exercita o caminho de falha de login.
SENHA_ERRADA = secrets.token_urlsafe(16)
