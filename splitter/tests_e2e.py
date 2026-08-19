"""Testes de ponta a ponta num navegador de verdade.

Rodam só no job `e2e` do CI (`pytest -m e2e`) e ficam de fora da suíte comum,
que não tem Playwright instalado. Para rodar na mão:

    pip install pytest-playwright && playwright install chromium
    pytest -m e2e

O que estes testes cobrem e os outros não: que a página chega ao navegador
inteira. Um `assertContains` do test client passa mesmo com o CSS quebrado,
com o JS levantando exceção ou com o `{% static %}` apontando para um arquivo
que não existe — nada disso é executado. Aqui é.

Vale mais aqui do que nos outros projetos: esta é a única tela do sistema, e
ela depende de JavaScript para montar a lista de arquivos e habilitar o envio.

Deliberadamente poucos. Suíte e2e grande envelhece mal, e a primeira falha
intermitente ensina a equipe a ignorar o vermelho.
"""

import pytest


@pytest.mark.e2e
def test_pagina_inicial_carrega_com_o_formulario(live_server, page):
    """A tela principal chega com o formulário de upload e o campo de arquivo."""
    page.goto(f"{live_server.url}/")

    assert page.locator("form").first.is_visible()
    assert page.locator('input[type="file"]').count() == 1


@pytest.mark.e2e
def test_estaticos_carregam_de_verdade(live_server, page):
    """O CSS chega ao navegador, e não só o HTML que o referencia.

    Um `{% static %}` apontando para arquivo inexistente passa em qualquer
    teste do test client, porque lá o CSS nunca é buscado. Aqui a cor computada
    do painel prova que a folha de estilo foi aplicada.
    """
    page.goto(f"{live_server.url}/")

    fundo = page.locator("header.ds-nav").evaluate("el => getComputedStyle(el).position")
    assert fundo != "static", "a barra de navegação saiu sem estilo — estáticos não carregaram"


@pytest.mark.e2e
def test_sem_erro_de_javascript_no_console(live_server, page):
    """A página monta a lista de arquivos por JS; exceção ali deixa o envio morto."""
    erros = []
    page.on("pageerror", lambda exc: erros.append(str(exc)))

    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("load")

    assert not erros, f"JavaScript quebrou ao carregar: {erros}"


@pytest.mark.e2e
def test_paginas_legais_abrem_sem_sessao(live_server, page):
    """Termos e privacidade precisam abrir para qualquer visitante — exigência legal."""
    from legal.models import DocumentoLegal, TipoDocumento

    # Sem documento publicado a view levanta Http404 de propósito, então o
    # cenário precisa ser semeado aqui. O `live_server` já traz o
    # `transactional_db`, que é o que permite gravar e o servidor enxergar:
    # ele roda noutra thread, e uma transação de teste comum não seria visível.
    for tipo in (TipoDocumento.TERMOS, TipoDocumento.PRIVACIDADE):
        documento = DocumentoLegal.objects.create(
            tipo=tipo,
            versao="1.0",
            titulo=f"Documento de teste ({tipo})",
            corpo_md="Conteúdo mínimo para a página renderizar.",
        )
        # `publicar()` gera o corpo_html sanitizado e põe o documento em vigor;
        # criar com status=publicado deixaria a página em branco.
        documento.publicar()

    for caminho in ("/termos/", "/privacidade/"):
        resposta = page.goto(f"{live_server.url}{caminho}")
        assert resposta is not None and resposta.status == 200, caminho
