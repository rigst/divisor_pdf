"""
URL configuration for config project.
"""

from django.contrib import admin
from django.urls import include, path

from core.healthz import healthz
from legal import views as legal_views

urlpatterns = [
    path("admin/", admin.site.urls),
    # Sonda do smoke-test do CD: banco, cache e broker; 200 ou 503.
    path("healthz/", healthz, name="healthz"),
    # Páginas legais (LGPD): acessíveis sem login. O texto vem do banco (app
    # `legal`), versionado — os nomes de rota seguem os mesmos de antes.
    path("privacidade/", legal_views.privacidade, name="privacidade"),
    path("termos/", legal_views.termos, name="termos"),
    path("legal/", include("legal.urls")),
    path("ocr/", include("ocr.urls")),
    path("", include("splitter.urls")),
]
