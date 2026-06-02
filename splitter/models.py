"""
Models do app Splitter.
Registra cada trabalho de divisão de PDF para auditoria e controle.
"""

from django.db import models


class SplitJob(models.Model):
    """
    Representa um trabalho de divisão de PDF.
    Armazena informações sobre o processamento para controle de sessão,
    status e limpeza automática.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendente'
        PROCESSING = 'processing', 'Processando'
        COMPLETED = 'completed', 'Concluído'
        FAILED = 'failed', 'Falhou'

    class CompressLevel(models.TextChoices):
        NONE = 'none', 'Sem Compressão'
        LOW = 'low', 'Qualidade Alta (Baixa Compressão)'
        MEDIUM = 'medium', 'Qualidade Média (Compressão Moderada)'
        HIGH = 'high', 'Qualidade Baixa (Alta Compressão)'

    # Identificação
    session_key = models.CharField(
        max_length=40,
        db_index=True,
        help_text='Chave da sessão do usuário'
    )
    task_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text='ID da task Celery'
    )

    # Informações do input
    original_filenames = models.JSONField(
        default=list,
        help_text='Lista de nomes dos PDFs enviados'
    )
    total_input_size_mb = models.FloatField(
        default=0,
        help_text='Tamanho total dos uploads em MB'
    )
    
    # Opções de Processamento
    compress_level = models.CharField(
        max_length=20,
        choices=CompressLevel.choices,
        default=CompressLevel.NONE,
        help_text='Nível de compressão desejado via Ghostscript'
    )
    should_split = models.BooleanField(
        default=True,
        help_text='Se deve dividir os PDFs em partes menores após compressão'
    )
    max_size_mb = models.FloatField(
        null=True,
        blank=True,
        help_text='Tamanho máximo solicitado pelo usuário em MB (opcional se não dividir)'
    )

    # Status e resultado
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )
    progress = models.IntegerField(
        default=0,
        help_text='Progresso do processamento (0-100)'
    )
    total_output_files = models.IntegerField(
        null=True,
        blank=True,
        help_text='Quantidade de PDFs gerados'
    )
    total_output_size_mb = models.FloatField(
        null=True,
        blank=True,
        help_text='Tamanho do arquivo ZIP resultante em MB'
    )
    output_zip_path = models.CharField(
        max_length=500,
        blank=True,
        help_text='Caminho do arquivo ZIP resultante'
    )
    error_message = models.TextField(
        blank=True,
        help_text='Mensagem de erro caso tenha falhado'
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cleaned_up = models.BooleanField(
        default=False,
        help_text='Se os arquivos já foram removidos do disco'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Trabalho de Divisão'
        verbose_name_plural = 'Trabalhos de Divisão'

    def __str__(self):
        filenames = ', '.join(self.original_filenames[:3])
        if len(self.original_filenames) > 3:
            filenames += f' (+{len(self.original_filenames) - 3})'
        return f'SplitJob #{self.pk} - {filenames} [{self.status}]'

    @property
    def session_dir(self):
        """Retorna o caminho do diretório da sessão."""
        from django.conf import settings
        from pathlib import Path
        return Path(settings.MEDIA_ROOT) / 'sessions' / self.session_key

    @property
    def input_dir(self):
        """Diretório de entrada dos PDFs originais."""
        return self.session_dir / 'input' / str(self.pk)

    @property
    def output_dir(self):
        """Diretório de saída dos PDFs divididos."""
        return self.session_dir / 'output' / str(self.pk)
