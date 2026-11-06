"""Permissions-Policy e CSP em todas as respostas (core/security_headers.py)."""

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from legal.testing import SENHA_TESTE

CSP = "Content-Security-Policy"
PP = "Permissions-Policy"
ADMIN_INDEX = "/admin/"
ADMIN_LOGIN = "/admin/login/"


def _grupo(padrao: str, texto: str) -> str:
    """Primeiro grupo do padrão; falha o teste (e não AttributeError) se não casar."""
    achado = re.search(padrao, texto)
    assert achado, f"{padrao!r} não aparece em {texto!r}"
    return achado.group(1)


class PermissionsPolicyTests(TestCase):
    def test_rota_publica_manda_o_valor_da_frota(self):
        resposta = self.client.get("/")
        self.assertEqual(
            resposta[PP],
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        )

    def test_healthz_e_admin_tambem_mandam(self):
        for caminho in ("/healthz/", ADMIN_LOGIN):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.client.get(caminho)[PP], settings.PERMISSIONS_POLICY)

    def test_admin_logado_manda(self):
        admin = get_user_model().objects.create_superuser("admin", "a@x.com", SENHA_TESTE)
        self.client.force_login(admin)
        resposta = self.client.get(ADMIN_INDEX)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta[PP], settings.PERMISSIONS_POLICY)

    @override_settings(PERMISSIONS_POLICY="")
    def test_valor_vazio_desliga_o_cabecalho(self):
        self.assertNotIn(PP, self.client.get("/"))


class ContentSecurityPolicyTests(TestCase):
    def test_publico_estrito_sem_unsafe_eval_nem_inline_em_script(self):
        politica = self.client.get("/")[CSP]
        script_src = _grupo(r"script-src ([^;]+)", politica)
        self.assertNotIn("unsafe-eval", script_src)
        self.assertNotIn("unsafe-inline", script_src)
        for diretiva in (
            "default-src 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ):
            self.assertIn(diretiva, politica)

    def test_nonce_da_politica_e_o_do_script_inline_do_tema(self):
        resposta = self.client.get("/")
        nonce = _grupo(r"'nonce-([^']+)'", resposta[CSP])
        self.assertContains(resposta, f'<script nonce="{nonce}">')

    def test_nonce_muda_a_cada_requisicao(self):
        def nonce():
            return _grupo(r"'nonce-([^']+)'", self.client.get("/")[CSP])

        self.assertNotEqual(nonce(), nonce())

    def test_admin_recebe_a_politica_com_unsafe_eval(self):
        politica = self.client.get(ADMIN_LOGIN)[CSP]
        self.assertIn("'unsafe-eval'", politica)
        self.assertIn("frame-ancestors 'none'", politica)

    def test_unsafe_eval_nao_vaza_para_fora_do_admin(self):
        for caminho in ("/", "/ocr/", "/healthz/"):
            with self.subTest(caminho=caminho):
                self.assertNotIn("unsafe-eval", self.client.get(caminho)[CSP])

    @override_settings(ENABLE_CSP=False)
    def test_desligada_por_setting(self):
        self.assertNotIn(CSP, self.client.get("/"))
