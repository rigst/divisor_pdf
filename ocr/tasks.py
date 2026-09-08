"""
Tasks Celery do app OCR.
Reconhecimento assíncrono de texto e limpeza dos arquivos expirados.
"""

import logging
import shutil
import zipfile
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _mb(caminho: Path) -> float:
    return round(caminho.stat().st_size / (1024 * 1024), 2)


def _zipar(arquivos: list[Path], destino: Path) -> Path:
    """Empacota os arquivos em um ZIP e apaga os originais."""
    with zipfile.ZipFile(str(destino), "w", zipfile.ZIP_DEFLATED) as zf:
        for arquivo in arquivos:
            zf.write(str(arquivo), arquivo.name)
    for arquivo in arquivos:
        arquivo.unlink(missing_ok=True)
    return destino


@shared_task(bind=True, max_retries=2, acks_late=True)
def process_ocr_job(self, job_id: int):
    """
    Processa um trabalho de OCR.

    1. Carrega o OCRJob do banco
    2. Roda o OCRmyPDF em cada PDF enviado
    3. Extrai o texto do PDF reconhecido e grava o `.md` correspondente
    4. Entrega os dois formatos direto (1 arquivo) ou em ZIP (vários)
    5. Remove os PDFs de entrada
    """
    from .models import OCRJob
    from .services import OCRProcessor, extrair_paginas, texto_para_markdown

    try:
        job = OCRJob.objects.get(pk=job_id)
    except OCRJob.DoesNotExist:
        logger.error(f"OCRJob #{job_id} não encontrado.")
        return

    job.status = OCRJob.Status.PROCESSING
    task_id = self.request.id or job.task_id or f"eager-{job_id}"
    job.task_id = task_id
    job.save(update_fields=["status", "task_id"])

    processing_warnings: list[str] = []

    try:
        input_dir = job.input_dir
        output_dir = job.output_dir
        if output_dir.exists():
            shutil.rmtree(str(output_dir))
        output_dir.mkdir(parents=True, exist_ok=True)

        input_files = sorted(f for f in input_dir.iterdir() if f.suffix.lower() == ".pdf")
        total_files = len(input_files)
        if total_files == 0:
            raise ValueError("Nenhum arquivo PDF encontrado para processamento.")

        processor = OCRProcessor(idioma=job.idioma, forcar=job.forcar_ocr)
        pdfs_gerados: list[Path] = []
        mds_gerados: list[Path] = []
        total_caracteres = 0

        for idx, pdf_path in enumerate(input_files):
            logger.info(f"[{idx + 1}/{total_files}] OCR: {pdf_path.name}")
            pdf_saida = output_dir / f"{pdf_path.stem}_ocr.pdf"

            try:
                processor.run(pdf_path, pdf_saida)
            except Exception as exc:
                # Um arquivo problemático não invalida os outros do mesmo envio.
                processing_warnings.append(f'"{pdf_path.name}": {exc}')
                logger.warning(f"OCR falhou para {pdf_path.name}: {exc}")
                continue

            paginas = extrair_paginas(pdf_saida)
            markdown = texto_para_markdown(paginas, pdf_path.stem)
            md_saida = output_dir / f"{pdf_path.stem}_ocr.md"
            md_saida.write_text(markdown, encoding="utf-8")

            caracteres = sum(len(p.strip()) for p in paginas)
            total_caracteres += caracteres
            if caracteres == 0:
                processing_warnings.append(
                    f'Nenhum texto foi reconhecido em "{pdf_path.name}". '
                    "O arquivo pode estar em branco ou com a digitalização ilegível."
                )

            pdfs_gerados.append(pdf_saida)
            mds_gerados.append(md_saida)

            job.progress = int(((idx + 1) / total_files) * 90)
            job.save(update_fields=["progress"])

        if not pdfs_gerados:
            raise RuntimeError(
                "Nenhum arquivo pôde ser reconhecido. " + " ".join(processing_warnings)
            )

        # Entrega: arquivo único vai direto; vários vão em um ZIP por formato.
        if len(pdfs_gerados) == 1:
            caminho_pdf, caminho_md = pdfs_gerados[0], mds_gerados[0]
        else:
            caminho_pdf = _zipar(pdfs_gerados, output_dir / "resultado_ocr_pdf.zip")
            caminho_md = _zipar(mds_gerados, output_dir / "resultado_ocr_md.zip")

        if input_dir.exists():
            shutil.rmtree(str(input_dir))

        job.status = OCRJob.Status.COMPLETED
        job.progress = 100
        job.total_output_files = len(pdfs_gerados)
        job.output_pdf_path = str(caminho_pdf)
        job.output_md_path = str(caminho_md)
        job.output_pdf_size_mb = _mb(caminho_pdf)
        job.output_md_size_mb = _mb(caminho_md)
        job.total_caracteres = total_caracteres
        job.completed_at = timezone.now()
        job.processing_warnings = processing_warnings
        job.save(
            update_fields=[
                "status",
                "progress",
                "total_output_files",
                "output_pdf_path",
                "output_md_path",
                "output_pdf_size_mb",
                "output_md_size_mb",
                "total_caracteres",
                "completed_at",
                "processing_warnings",
            ]
        )

        logger.info(
            f"OCRJob #{job_id} concluído: {job.total_output_files} arquivo(s), "
            f"{total_caracteres} caractere(s) reconhecido(s)."
        )

    except Exception as exc:
        logger.exception(f"Erro fatal ao processar OCRJob #{job_id}")
        job.processing_warnings = processing_warnings

        # Em modo eager não há backend de resultados para sustentar o retry.
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            job.status = OCRJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message", "processing_warnings"])
            return

        if self.request.retries >= self.max_retries:
            job.status = OCRJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message", "processing_warnings"])
        else:
            job.status = OCRJob.Status.PROCESSING
            job.error_message = ""
            job.save(update_fields=["status", "error_message", "processing_warnings"])

        raise self.retry(exc=exc, countdown=30) from exc


@shared_task
def cleanup_expired_ocr_jobs():
    """
    Remove do disco os arquivos de jobs de OCR que passaram da retenção.

    Espelha `splitter.tasks.cleanup_expired_sessions`: aquela varre apenas os
    SplitJob, e os diretórios `ocr_input`/`ocr_output` ficariam para trás.
    """
    from .models import OCRJob

    retention = getattr(settings, "FILE_RETENTION_SECONDS", 3600)
    cutoff = timezone.now() - timedelta(seconds=retention)

    expirados = OCRJob.objects.filter(created_at__lt=cutoff, cleaned_up=False).exclude(
        status=OCRJob.Status.PROCESSING
    )

    removidos = 0
    for job in expirados:
        try:
            for diretorio in (job.output_dir, job.input_dir):
                if diretorio.exists():
                    shutil.rmtree(str(diretorio))

            job.cleaned_up = True
            job.save(update_fields=["cleaned_up"])
            removidos += 1
        except OSError:
            logger.exception(f"Erro ao limpar OCRJob #{job.pk}")

    if removidos > 0:
        logger.info(f"Limpeza de OCR: {removidos} job(s) removido(s)")
