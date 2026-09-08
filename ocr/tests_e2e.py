"""Testes de ponta a ponta da tela de OCR, num navegador de verdade.

Rodam só no job `e2e` do CI (`pytest -m e2e`), como os do `splitter`. Aqui o
que se prova é o caminho que o test client não percorre: que o botão da tela
principal chega ao navegador apontando para o lugar certo e que o JavaScript
desta página monta sem exceção — sem ele o envio nunca é habilitado.
"""

import pytest


@pytest.mark.e2e
def test_botao_da_tela_principal_leva_ao_ocr(live_server, page):
    """O caminho que o usuário faz: entra na home, clica, chega no OCR."""
    page.goto(f"{live_server.url}/")

    page.click("text=Fazer OCR de um PDF")
    page.wait_for_url("**/ocr/")

    assert page.locator('input[type="file"]').count() == 1


@pytest.mark.e2e
def test_sem_erro_de_javascript_no_console_do_ocr(live_server, page):
    """A tela depende de JS para listar arquivos e liberar o botão de envio."""
    erros = []
    page.on("pageerror", lambda exc: erros.append(str(exc)))

    page.goto(f"{live_server.url}/ocr/")
    page.wait_for_load_state("load")

    assert not erros, f"JavaScript quebrou ao carregar: {erros}"
