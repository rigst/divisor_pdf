"""Testes do app `legal` no divisor — o único projeto com aceite anônimo."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from legal.testing import SENHA_TESTE
from splitter.models import SplitJob

from . import documentos_io
from .forms import AceiteForm
from .models import AceiteLegal, DocumentoLegal, OrigemAceite, StatusDocumento, TipoDocumento
from .services import aceite_anonimo_valido, documentos_vigentes
from .utils import calcular_sha256, ip_do_request, renderizar_markdown

Usuario = get_user_model()


def criar_documento(
    tipo=TipoDocumento.TERMOS,
    versao="1.0",
    *,
    publicar=True,
    material=True,
    corpo="# Título\n\nTexto do documento.\n",
):
    documento = DocumentoLegal.objects.create(
        tipo=tipo, versao=versao, titulo=f"Doc {tipo}", corpo_md=corpo, material=material
    )
    if publicar:
        documento.publicar()
    return documento


def pdf_falso(nome="a.pdf"):
    conteudo = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    return SimpleUploadedFile(nome, conteudo, content_type="application/pdf")


class UtilsTests(TestCase):
    def test_ip_usa_ultimo_item_do_forwarded_for(self):
        request = self.client.request().wsgi_request
        request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.1, 203.0.113.9"
        self.assertEqual(ip_do_request(request), "203.0.113.9")

    def test_markdown_e_sanitizado(self):
        html = renderizar_markdown("Texto <script>alert(1)</script> normal.")
        self.assertNotIn("<script>", html)

    def test_hash_ignora_quebra_de_linha_e_espaco_final(self):
        self.assertEqual(calcular_sha256("a\r\nb  \n"), calcular_sha256("a\nb\n"))


class DocumentoLegalTests(TestCase):
    def test_publicar_congela_html_e_hash(self):
        documento = criar_documento()
        self.assertEqual(documento.status, StatusDocumento.PUBLICADO)
        self.assertEqual(documento.sha256, calcular_sha256(documento.corpo_md))

    def test_publicar_arquiva_versao_anterior(self):
        antiga = criar_documento(versao="1.0")
        criar_documento(versao="1.1")
        antiga.refresh_from_db()
        self.assertEqual(antiga.status, StatusDocumento.ARQUIVADO)


class AceiteFormTests(TestCase):
    def test_rejeita_sem_marcar(self):
        self.assertFalse(AceiteForm(data={}).is_valid())

    def test_aceita_marcado(self):
        self.assertTrue(AceiteForm(data={"aceite_legal": "on"}).is_valid())


class PaginasPublicasTests(TestCase):
    def test_termos_e_privacidade_saem_do_banco(self):
        criar_documento(corpo="# Termos\n\nConteúdo publicado.\n")
        criar_documento(tipo=TipoDocumento.PRIVACIDADE, corpo="# Privacidade\n\nOutro texto.\n")
        self.assertContains(self.client.get(reverse("termos")), "Conteúdo publicado.")
        self.assertContains(self.client.get(reverse("privacidade")), "Outro texto.")

    def test_sem_versao_publicada_responde_404(self):
        self.assertEqual(self.client.get(reverse("termos")).status_code, 404)

    def test_versao_arquivada_continua_consultavel(self):
        antiga = criar_documento(versao="1.0", corpo="# V1\n\nTexto antigo.\n")
        criar_documento(versao="2.0", corpo="# V2\n\nTexto novo.\n")
        resposta = self.client.get(reverse("legal:versao", args=[antiga.tipo, antiga.versao]))
        self.assertContains(resposta, "Texto antigo.")

    def test_rascunho_nao_e_publico(self):
        rascunho = criar_documento(versao="9.0", publicar=False)
        resposta = self.client.get(reverse("legal:versao", args=[rascunho.tipo, rascunho.versao]))
        self.assertEqual(resposta.status_code, 404)


class AceiteAnonimoTests(TestCase):
    """O caminho que só existe aqui: aceite sem conta, preso à sessão."""

    def setUp(self):
        self.termos = criar_documento()
        self.privacidade = criar_documento(tipo=TipoDocumento.PRIVACIDADE)
        self.url = reverse("splitter:upload")

    def _upload(self, dados=None, **extra):
        payload = {
            "files": pdf_falso(),
            "compress_level": "none",
            "should_split": "true",
            "max_size_mb": "1",
        }
        payload.update(dados or {})
        return self.client.post(self.url, payload, **extra)

    def test_upload_sem_aceite_e_recusado(self):
        resposta = self._upload()

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Termos de Uso", resposta.json()["error"])
        self.assertFalse(SplitJob.objects.exists())
        self.assertFalse(AceiteLegal.objects.exists())

    def test_upload_com_aceite_registra_prova_completa(self):
        resposta = self._upload(
            dados={"aceite_legal": "on"},
            HTTP_X_FORWARDED_FOR="198.51.100.1, 203.0.113.9",
            HTTP_USER_AGENT="Mozilla/5.0 (Teste)",
        )

        self.assertEqual(resposta.status_code, 202)
        aceites = AceiteLegal.objects.all()
        self.assertEqual(aceites.count(), 2)  # termos + privacidade

        aceite = aceites.get(documento=self.termos)
        self.assertIsNone(aceite.usuario)
        self.assertEqual(aceite.origem, OrigemAceite.UPLOAD_ANONIMO)
        self.assertEqual(aceite.ip, "203.0.113.9")
        self.assertEqual(aceite.user_agent, "Mozilla/5.0 (Teste)")
        self.assertEqual(aceite.documento_sha256, self.termos.sha256)
        self.assertTrue(aceite.integro)
        self.assertTrue(aceite.session_key)

    def test_segundo_upload_da_mesma_sessao_nao_pede_aceite_de_novo(self):
        self._upload(dados={"aceite_legal": "on"})
        self.assertEqual(AceiteLegal.objects.count(), 2)

        resposta = self._upload()  # sem o checkbox

        self.assertEqual(resposta.status_code, 202)
        self.assertEqual(AceiteLegal.objects.count(), 2)  # não duplica

    def test_versao_nova_volta_a_exigir_aceite_na_mesma_sessao(self):
        self._upload(dados={"aceite_legal": "on"})
        criar_documento(versao="2.0")

        resposta = self._upload()

        self.assertEqual(resposta.status_code, 400)

    def test_checkbox_aparece_na_home_e_nunca_marcado(self):
        import re

        html = self.client.get(reverse("splitter:index")).content.decode()
        tags = re.findall(r"<input[^>]*name=\"aceite_legal\"[^>]*>", html)

        self.assertEqual(len(tags), 1)
        self.assertNotIn("checked", tags[0])
        self.assertIn("required", tags[0])

    def test_checkbox_some_depois_de_aceito(self):
        self._upload(dados={"aceite_legal": "on"})
        html = self.client.get(reverse("splitter:index")).content.decode()
        self.assertNotIn('name="aceite_legal"', html)

    def test_template_nao_vaza_comentario_de_desenvolvimento(self):
        html = self.client.get(reverse("splitter:index")).content.decode()
        self.assertNotIn("initial=False", html)


class AdminImutabilidadeTests(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from .admin import DocumentoLegalAdmin

        self.admin = DocumentoLegalAdmin(DocumentoLegal, AdminSite())
        self.request = self.client.request().wsgi_request
        self.request.user = Usuario.objects.create_superuser(username="root", password=SENHA_TESTE)

    def test_rascunho_e_editavel(self):
        rascunho = criar_documento(publicar=False)
        self.assertTrue(self.admin.has_change_permission(self.request, rascunho))

    def test_publicado_nao_e_editavel_nem_apagavel(self):
        documento = criar_documento()
        self.assertFalse(self.admin.has_change_permission(self.request, documento))
        self.assertFalse(self.admin.has_delete_permission(self.request, documento))

    def test_aceite_e_somente_leitura(self):
        from django.contrib.admin.sites import AdminSite

        from .admin import AceiteLegalAdmin

        aceite_admin = AceiteLegalAdmin(AceiteLegal, AdminSite())
        self.assertFalse(aceite_admin.has_add_permission(self.request))
        self.assertFalse(aceite_admin.has_change_permission(self.request))
        self.assertFalse(aceite_admin.has_delete_permission(self.request))


class CommandsTests(TestCase):
    def test_exportar_reproduz_o_texto_publicado(self):
        corpo = "# Termos\n\nTexto **exato** publicado.\n"
        documento = criar_documento(versao="7.7", corpo=corpo)
        call_command("exportar_documentos_legais", verbosity=0)

        arquivo = documentos_io.caminho(documento.tipo, documento.versao)
        self.addCleanup(arquivo.unlink, missing_ok=True)

        _metadados, corpo_lido = documentos_io.ler(arquivo)
        self.assertEqual(calcular_sha256(corpo_lido), documento.sha256)

    def test_importar_nao_sobrescreve_versao_existente(self):
        documento = criar_documento(versao="8.8", corpo="# Original\n\nTexto.\n")
        arquivo = documentos_io.escrever(
            documento.tipo, documento.versao, {"titulo": "Adulterado"}, "# Outro\n\nTrocado.\n"
        )
        self.addCleanup(arquivo.unlink, missing_ok=True)

        with self.assertRaises(SystemExit):
            call_command("importar_documentos_legais", verbosity=0)

        documento.refresh_from_db()
        self.assertIn("Original", documento.corpo_md)


class ServicesTests(TestCase):
    def test_documentos_vigentes_traz_um_por_tipo(self):
        criar_documento(versao="1.0")
        criar_documento(versao="2.0")
        criar_documento(tipo=TipoDocumento.PRIVACIDADE)

        vigentes = documentos_vigentes()
        self.assertEqual(set(vigentes), {"termos", "privacidade"})
        self.assertEqual(vigentes["termos"].versao, "2.0")

    def test_sem_documento_publicado_o_upload_nao_e_bloqueado(self):
        """Sem política publicada não há o que aceitar — o serviço não trava."""
        request = self.client.request().wsgi_request
        self.assertTrue(aceite_anonimo_valido(request))


class AceiteObrigatorioMiddlewareTests(TestCase):
    """Este projeto não ativa o middleware — não tem contas, e o aceite é
    anônimo (ver o comentário em config/settings/base.py). Mas o app `legal` é
    copiado entre os projetos, e nos que têm conta é este middleware que
    obriga o re-aceite. Testar a cópia daqui protege as de lá."""

    def _middleware(self, resposta="ok"):
        from legal.middleware import AceiteObrigatorioMiddleware

        return AceiteObrigatorioMiddleware(lambda request: resposta)

    def test_anonimo_passa_direto(self):
        from django.test import RequestFactory

        pedido = RequestFactory().get("/")
        pedido.user = AnonymousUser()

        self.assertEqual(self._middleware()(pedido), "ok")

    def test_prefixo_extra_do_projeto_entra_na_allowlist(self):
        """LEGAL_ALLOWLIST_EXTRA existe para o trilhas liberar /sw.js: se o
        service worker levasse 302 para o aceite, o navegador cacharia o
        redirecionamento."""
        with self.settings(LEGAL_ALLOWLIST_EXTRA=("/sw.js",)):
            middleware = self._middleware()

        self.assertIn("/sw.js", middleware.prefixos)
        self.assertIn("/termos/", middleware.prefixos)


class ExportarAceitesCsvTests(TestCase):
    """A exportação em CSV é a via prática de responder a um pedido de titular
    (LGPD) e de auditar aceites — e não tinha teste."""

    def test_csv_traz_cabecalho_e_uma_linha_por_aceite(self):
        from django.contrib.admin.sites import AdminSite

        from legal.admin import AceiteLegalAdmin
        from legal.models import AceiteLegal

        documento = criar_documento()
        pedido = self.client.request().wsgi_request
        from legal.services import registrar_aceite

        registrar_aceite(pedido, origem="teste", e_visitante=True)

        admin_obj = AceiteLegalAdmin(AceiteLegal, AdminSite())
        resposta = admin_obj.exportar_csv(pedido, AceiteLegal.objects.all())

        self.assertEqual(resposta["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("aceites.csv", resposta["Content-Disposition"])

        linhas = resposta.content.decode("utf-8").strip().splitlines()
        self.assertEqual(len(linhas), 2)  # cabeçalho + o aceite
        self.assertIn("sha256_aceito", linhas[0])
        self.assertIn("integro", linhas[0])
        self.assertIn(documento.versao, linhas[1])
        self.assertIn("sim", linhas[1])  # e_visitante
