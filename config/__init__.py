"""
Inicialização do pacote config.
Importa o app Celery para que seja carregado quando o Django inicia.
"""

from .celery import app as celery_app

__all__ = ('celery_app',)
