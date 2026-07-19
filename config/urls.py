"""
URL configuration for config project.
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

urlpatterns = [
    path('admin/', admin.site.urls),
    # Páginas legais (LGPD): acessíveis sem login.
    path('privacidade/', TemplateView.as_view(template_name='legal/privacidade.html'), name='privacidade'),
    path('termos/', TemplateView.as_view(template_name='legal/termos.html'), name='termos'),
    path('', include('splitter.urls')),
]
