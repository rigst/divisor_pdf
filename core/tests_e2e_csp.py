"""CSP no navegador: as telas abrem sem nenhuma violação da política.

Os testes de `tests_security_headers.py` provam que o cabeçalho sai; só um
navegador de verdade prova que a política não bloqueia nada que a tela usa
(script inline sem nonce, fonte, imagem `data:`, o Alpine.js do admin). Cada
tela é aberta com dois detectores: o evento `securitypolicyviolation` do
documento e a mensagem "Content Security Policy" que o Chromium põe no console.
"""

import pytest

from legal.testing import SENHA_TESTE

# Registrado antes de qualquer script da página: pega também violação do
# script inline do <head>, que roda antes do resto.
COLETOR = """
window.__violacoesCsp = [];
document.addEventListener('securitypolicyviolation', (e) => {
    window.__violacoesCsp.push(`${e.violatedDirective} bloqueou ${e.blockedURI || 'inline'}`);
});
"""

PDF_MINIMO = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 10 10]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def _vigiar(page):
    erros = []
    page.add_init_script(COLETOR)
    page.on(
        "console",
        lambda msg: erros.append(msg.text) if "Content Security Policy" in msg.text else None,
    )
    return erros


def _abrir(page, url):
    resposta = page.goto(url)
    assert resposta is not None and resposta.status == 200, url
    page.wait_for_load_state("networkidle")
    return page.evaluate("window.__violacoesCsp")


def _semear_documentos_legais():
    from legal.models import DocumentoLegal, TipoDocumento

    for tipo in (TipoDocumento.TERMOS, TipoDocumento.PRIVACIDADE):
        DocumentoLegal.objects.create(
            tipo=tipo, versao="1.0", titulo=f"Teste ({tipo})", corpo_md="Texto **mínimo**."
        ).publicar()


@pytest.mark.e2e
def test_telas_publicas_sem_violacao_de_csp(live_server, page, tmp_path):
    _semear_documentos_legais()
    erros = _vigiar(page)
    violacoes = {}

    for caminho in ("/", "/ocr/", "/termos/", "/privacidade/"):
        violacoes[caminho] = _abrir(page, f"{live_server.url}{caminho}")

    # A lista de arquivos e os controles são montados por JS ao escolher o PDF:
    # é a parte da tela que mais mexe no DOM.
    pdf = tmp_path / "teste.pdf"
    pdf.write_bytes(PDF_MINIMO)
    for caminho in ("/", "/ocr/"):
        _abrir(page, f"{live_server.url}{caminho}")
        page.locator('input[type="file"]').set_input_files(str(pdf))
        page.wait_for_timeout(300)
        violacoes[f"{caminho} com arquivo"] = page.evaluate("window.__violacoesCsp")

    # O script inline do tema rodou (com o nonce): ele marca o <html> com .js.
    assert page.evaluate("document.documentElement.classList.contains('js')")
    assert not any(violacoes.values()), violacoes
    assert not erros, erros


@pytest.mark.e2e
def test_admin_logado_sem_violacao_de_csp(live_server, page):
    from django.contrib.auth import get_user_model

    _semear_documentos_legais()
    get_user_model().objects.create_superuser("admin", "admin@example.com", SENHA_TESTE)
    erros = _vigiar(page)

    violacoes = {"/admin/login/": _abrir(page, f"{live_server.url}/admin/login/?next=/admin/")}
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', SENHA_TESTE)
    page.click('button[type="submit"]')
    page.wait_for_url(f"{live_server.url}/admin/")

    for caminho in (
        "/admin/",
        "/admin/splitter/splitjob/",
        "/admin/ocr/ocrjob/",
        "/admin/legal/documentolegal/",
        "/admin/legal/documentolegal/add/",
        "/admin/legal/documentolegal/1/change/",
        "/admin/legal/aceitelegal/",
        "/admin/auth/user/",
        "/admin/auth/user/1/change/",
    ):
        violacoes[caminho] = _abrir(page, f"{live_server.url}{caminho}")

    # O Alpine.js do unfold inicializou (precisa do 'unsafe-eval' do admin).
    assert page.evaluate("typeof window.Alpine === 'object'")
    assert not any(violacoes.values()), violacoes
    assert not erros, erros
