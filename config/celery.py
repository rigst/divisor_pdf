"""
Configuração do Celery para o projeto Divisor de PDFs.
"""

import os
from celery import Celery

# Define o módulo de configuração padrão do Django para o Celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

app = Celery('config')

# Carrega configurações do Django com prefixo CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-descobre tasks nos apps instalados
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Task de debug para verificar se o Celery está funcionando."""
    print(f'Request: {self.request!r}')
