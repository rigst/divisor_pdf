"""Cabeçalhos de segurança que o SecurityMiddleware do Django não manda.

O Django já cuida de HSTS, nosniff, Referrer-Policy e COOP; faltavam a
Content-Security-Policy e a Permissions-Policy. Ficam aqui, no mesmo padrão
dos outros apps da frota (sistema_orcamentos), com o valor em settings.
"""

import secrets

from django.conf import settings


class PermissionsPolicyMiddleware:
    """Desliga as APIs do navegador que o app não usa (câmera, microfone...).

    O divisor só envia arquivos e baixa o resultado: nenhuma tela pede câmera,
    microfone, localização, pagamento ou USB. `setdefault` deixa uma view
    específica declarar a própria política, se um dia precisar.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        politica = getattr(settings, "PERMISSIONS_POLICY", "")
        if politica:
            response.setdefault("Permissions-Policy", politica)
        return response


class ContentSecurityPolicyMiddleware:
    """Aplica a CSP, com uma política própria (e mais frouxa) para o /admin/.

    O app público fica na política estrita: script só do próprio domínio ou com
    o nonce da requisição (o único script inline é o do tema claro/escuro no
    base.html, que precisa rodar antes do CSS). O admin não cabe nela porque o
    django-unfold usa Alpine.js, que compila as expressões de `x-data`/`x-init`
    com `new Function()` e exige 'unsafe-eval'. A exceção fica presa ao prefixo
    do admin, que só quem tem `is_staff` alcança.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _politica(self, request):
        prefixo = getattr(settings, "ADMIN_PATH_PREFIX", "/admin/")
        if request.path.startswith(prefixo):
            return (
                getattr(settings, "CONTENT_SECURITY_POLICY_ADMIN", "")
                or settings.CONTENT_SECURITY_POLICY
            )
        return settings.CONTENT_SECURITY_POLICY

    def __call__(self, request):
        nonce = secrets.token_urlsafe(16)
        request.csp_nonce = nonce
        response = self.get_response(request)
        if getattr(settings, "ENABLE_CSP", False) and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self._politica(request).format(nonce=nonce)
        return response
