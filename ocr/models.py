"""
Models do app OCR.
Registra cada trabalho de reconhecimento de texto para controle de sessão,
status e limpeza automática — mesmo desenho do SplitJob do app `splitter`.
"""

from django.db import models


class OCRJob(models.Model):
    """
    Representa um trabalho de OCR sobre um ou mais PDFs.

    O resultado sai em dois formatos: o PDF pesquisável (texto invisível sobre
    a imagem original) e o texto extraído em Markdown. Os dois são gerados
    sempre; quem escolhe o que baixar é o usuário, na tela de resultado.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        PROCESSING = "processing", "Processando"
        COMPLETED = "completed", "Concluído"
        FAILED = "failed", "Falhou"

    class Idioma(models.TextChoices):
        POR = "por", "Português"
        ENG = "eng", "Inglês"
        POR_ENG = "por+eng", "Português + Inglês"

    class Formato(models.TextChoices):
        PDF = "pdf", "PDF pesquisável"
        MD = "md", "Markdown (.md)"

    # Identificação
    session_key = models.CharField(
        max_length=40, db_index=True, help_text="Chave da sessão do usuário"
    )
    task_id = models.CharField(
        max_length=255, unique=True, null=True, blank=True, help_text="ID da task Celery"
    )

    # Informações do input
    original_filenames = models.JSONField(
        default=list, help_text="Lista de nomes dos PDFs enviados"
    )
    total_input_size_mb = models.FloatField(default=0, help_text="Tamanho total dos uploads em MB")

    # Opções de processamento
    idioma = models.CharField(
        max_length=20,
        choices=Idioma.choices,
        default=Idioma.POR,
        help_text="Idioma(s) usados pelo Tesseract no reconhecimento",
    )
    forcar_ocr = models.BooleanField(
        default=False,
        help_text=(
            "Refaz o OCR mesmo em páginas que já têm texto. "
            "Sem isso, páginas com texto nativo são preservadas como estão."
        ),
    )

    # Status e resultado
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    progress = models.IntegerField(default=0, help_text="Progresso do processamento (0-100)")
    total_output_files = models.IntegerField(
        null=True, blank=True, help_text="Quantidade de PDFs reconhecidos"
    )
    output_pdf_path = models.CharField(
        max_length=500, blank=True, help_text="Caminho do PDF pesquisável (ou do ZIP com eles)"
    )
    output_md_path = models.CharField(
        max_length=500, blank=True, help_text="Caminho do Markdown (ou do ZIP com eles)"
    )
    output_pdf_size_mb = models.FloatField(null=True, blank=True, help_text="Tamanho do PDF em MB")
    output_md_size_mb = models.FloatField(null=True, blank=True, help_text="Tamanho do .md em MB")
    total_caracteres = models.IntegerField(
        null=True, blank=True, help_text="Caracteres reconhecidos, somando todos os arquivos"
    )
    error_message = models.TextField(blank=True, help_text="Mensagem de erro caso tenha falhado")
    processing_warnings = models.JSONField(
        default=list, blank=True, help_text="Avisos sobre limitações encontradas no processamento"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cleaned_up = models.BooleanField(
        default=False, help_text="Se os arquivos já foram removidos do disco"
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Trabalho de OCR"
        verbose_name_plural = "Trabalhos de OCR"

    def __str__(self):
        filenames = ", ".join(self.original_filenames[:3])
        if len(self.original_filenames) > 3:
            filenames += f" (+{len(self.original_filenames) - 3})"
        return f"OCRJob #{self.pk} - {filenames} [{self.status}]"

    @property
    def session_dir(self):
        """Diretório da sessão, compartilhado com o app `splitter`."""
        from pathlib import Path

        from django.conf import settings

        return Path(settings.MEDIA_ROOT) / "sessions" / self.session_key

    @property
    def input_dir(self):
        """Diretório de entrada dos PDFs originais.

        Prefixo `ocr_` de propósito: a sessão é a mesma do divisor, e dois jobs
        de apps diferentes podem ter o mesmo `pk`.
        """
        return self.session_dir / "ocr_input" / str(self.pk)

    @property
    def output_dir(self):
        """Diretório de saída dos arquivos reconhecidos."""
        return self.session_dir / "ocr_output" / str(self.pk)

    def caminho_do_formato(self, formato: str) -> str:
        """Caminho em disco do resultado no formato pedido ('pdf' ou 'md')."""
        return self.output_pdf_path if formato == self.Formato.PDF else self.output_md_path
