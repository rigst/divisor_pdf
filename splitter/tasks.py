"""
Tasks Celery do app Splitter.
Processamento assíncrono de divisão de PDFs e limpeza de sessões expiradas.
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


@shared_task(bind=True, max_retries=2, acks_late=True)
def process_split_job(self, job_id: int):
    """
    Processa um trabalho de divisão e/ou compressão de PDF.

    1. Carrega o SplitJob do banco
    2. Aplica compressão Ghostscript nos PDFs se solicitado
    3. Aplica divisão de PDF em partes menores se solicitado
    4. Empacota todos os resultados em um ZIP
    5. Salva dados de tamanho final e status no model
    6. Remove os PDFs temporários de entrada e intermediários
    """
    from .models import SplitJob
    from .services import PDFSplitter, PDFCompressor

    try:
        job = SplitJob.objects.get(pk=job_id)
    except SplitJob.DoesNotExist:
        logger.error(f'SplitJob #{job_id} não encontrado.')
        return

    # Marca como processando
    job.status = SplitJob.Status.PROCESSING
    task_id = self.request.id or job.task_id or f'eager-{job_id}'
    job.task_id = task_id
    job.save(update_fields=['status', 'task_id'])

    try:
        input_dir = job.input_dir
        output_dir = job.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        input_files = sorted(input_dir.glob('*.pdf'))
        total_files = len(input_files)

        if total_files == 0:
            raise ValueError('Nenhum arquivo PDF encontrado para processamento.')

        all_output_files = []
        
        # Define se usaremos compressão
        use_compression = job.compress_level != SplitJob.CompressLevel.NONE
        
        for idx, pdf_path in enumerate(input_files):
            current_target_pdf = pdf_path
            
            # 1. Compressão opcional via Ghostscript
            if use_compression:
                logger.info(f'[{idx + 1}/{total_files}] Comprimindo: {pdf_path.name}')
                
                # Criar pasta temporária para arquivos comprimidos
                compressed_dir = input_dir / 'compressed'
                compressed_dir.mkdir(parents=True, exist_ok=True)
                compressed_path = compressed_dir / f'compressed_{pdf_path.name}'
                
                compressor = PDFCompressor(job.compress_level)
                compressor.compress(str(pdf_path), str(compressed_path))
                
                # Redireciona o target para o arquivo comprimido
                current_target_pdf = compressed_path
                
                # Atualizar progresso parcial (até 45% do job total para compressão)
                progress = int(((idx + 1) / total_files) * 45)
                job.progress = progress
                job.save(update_fields=['progress'])

            # 2. Divisão opcional via pypdf
            if job.should_split:
                logger.info(f'[{idx + 1}/{total_files}] Dividindo: {current_target_pdf.name}')
                
                max_size_bytes = int(job.max_size_mb * 1024 * 1024)
                splitter_compress_level = (
                    SplitJob.CompressLevel.NONE if use_compression else job.compress_level
                )
                splitter = PDFSplitter(max_size_bytes, compress_level=splitter_compress_level)
                
                # O splitter grava os arquivos diretamente na pasta de output
                temp_output_files = splitter.split(
                    input_path=str(current_target_pdf),
                    output_dir=str(output_dir),
                    base_name=pdf_path.stem  # Mantém o nome amigável do arquivo original
                )
                
                # Nomenclatura dinâmica
                final_split_files = []
                if len(temp_output_files) == 1:
                    old_path = Path(temp_output_files[0])
                    suffix = '_comprimido_dividido' if use_compression else '_dividido'
                    new_path = old_path.parent / f'{pdf_path.stem}{suffix}.pdf'
                    old_path.rename(new_path)
                    final_split_files.append(str(new_path))
                else:
                    for p_idx, file_str in enumerate(temp_output_files):
                        old_path = Path(file_str)
                        prefix = '_comprimido' if use_compression else ''
                        new_path = old_path.parent / f'{pdf_path.stem}{prefix}_parte{p_idx + 1:03d}.pdf'
                        old_path.rename(new_path)
                        final_split_files.append(str(new_path))
                
                all_output_files.extend(final_split_files)
                
                # Atualiza progresso parcial (de 45% a 90% para divisão)
                base_progress = 45 if use_compression else 0
                factor = 45 if use_compression else 90
                progress = base_progress + int(((idx + 1) / total_files) * factor)
                job.progress = progress
                job.save(update_fields=['progress'])
            else:
                # Caso não divida, o próprio arquivo (comprimido ou original) é enviado para a saída
                # Copiar para a pasta de saída com sufixo amigável
                suffix = '_comprimido' if use_compression else ''
                dest_name = f'{pdf_path.stem}{suffix}.pdf'
                dest_path = output_dir / dest_name
                shutil.copy2(str(current_target_pdf), str(dest_path))
                all_output_files.append(str(dest_path))
                
                # Atualiza progresso simples
                progress = int(((idx + 1) / total_files) * 90)
                job.progress = progress
                job.save(update_fields=['progress'])

        # 3. Criação do arquivo ZIP final ou entrega direta se for arquivo único
        if len(all_output_files) == 1:
            final_file_path = Path(all_output_files[0])
            logger.info(f'Apenas 1 arquivo gerado: {final_file_path.name}. Disponibilizando diretamente.')

            # Remove todo o diretório temporário de entrada (com originais e comprimidos)
            if input_dir.exists():
                shutil.rmtree(str(input_dir))

            # Calcula tamanho em MB
            file_size_mb = round(final_file_path.stat().st_size / (1024 * 1024), 2)

            # Atualiza o job como concluído com o arquivo único
            job.status = SplitJob.Status.COMPLETED
            job.progress = 100
            job.total_output_files = 1
            job.total_output_size_mb = file_size_mb
            job.output_zip_path = str(final_file_path)
            job.completed_at = timezone.now()
            job.save(update_fields=[
                'status', 'progress', 'total_output_files',
                'total_output_size_mb', 'output_zip_path', 'completed_at'
            ])

            logger.info(
                f'SplitJob #{job_id} concluído com sucesso (arquivo único). '
                f'Tamanho final {file_size_mb} MB'
            )
        else:
            zip_path = output_dir / 'resultado.zip'
            logger.info(f'Criando ZIP final contendo {len(all_output_files)} arquivo(s)...')

            with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_path in all_output_files:
                    file_path = Path(file_path)
                    zf.write(str(file_path), file_path.name)

            # Remove os PDFs individuais gerados de output para liberar espaço, mantendo só o ZIP
            for file_path in all_output_files:
                Path(file_path).unlink(missing_ok=True)

            # Remove todo o diretório temporário de entrada (com originais e comprimidos)
            if input_dir.exists():
                shutil.rmtree(str(input_dir))

            # Calcula tamanho do arquivo ZIP final em MB
            zip_size_mb = round(zip_path.stat().st_size / (1024 * 1024), 2)

            # Atualiza o job como concluído
            job.status = SplitJob.Status.COMPLETED
            job.progress = 100
            job.total_output_files = len(all_output_files)
            job.total_output_size_mb = zip_size_mb
            job.output_zip_path = str(zip_path)
            job.completed_at = timezone.now()
            job.save(update_fields=[
                'status', 'progress', 'total_output_files',
                'total_output_size_mb', 'output_zip_path', 'completed_at'
            ])

            logger.info(
                f'SplitJob #{job_id} concluído com sucesso. '
                f'{len(all_output_files)} arquivo(s), tamanho final {zip_size_mb} MB'
            )

    except Exception as exc:
        logger.exception(f'Erro fatal ao processar SplitJob #{job_id}')
        job.status = SplitJob.Status.FAILED
        job.error_message = str(exc)
        job.save(update_fields=['status', 'error_message'])

        # Em modo eager, não tenta retry (que dependeria de backend de resultados)
        if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
            return  # Job já marcado como FAILED, a view tratará o status
        # Em produção, permite retrying via Celery
        raise self.retry(exc=exc, countdown=30)


@shared_task
def cleanup_expired_sessions():
    """
    Limpa arquivos de sessões expiradas.

    Roda periodicamente via Celery Beat.
    Remove do disco e marca os jobs como cleaned_up.
    """
    from .models import SplitJob

    retention = getattr(settings, 'FILE_RETENTION_SECONDS', 3600)
    cutoff = timezone.now() - timedelta(seconds=retention)

    # Busca jobs concluídos ou falhados que passaram do tempo de retenção
    expired_jobs = SplitJob.objects.filter(
        created_at__lt=cutoff,
        cleaned_up=False
    ).exclude(status=SplitJob.Status.PROCESSING)

    cleaned_count = 0
    for job in expired_jobs:
        try:
            # Remove o diretório de output
            if job.output_dir.exists():
                shutil.rmtree(str(job.output_dir))
                logger.info(f'Removido output de SplitJob #{job.pk}')

            # Remove o diretório de input (caso ainda exista)
            if job.input_dir.exists():
                shutil.rmtree(str(job.input_dir))

            # Remove diretório da sessão se estiver vazio
            session_dir = job.session_dir
            if session_dir.exists():
                # Verifica se há outros jobs usando o mesmo diretório
                try:
                    remaining = list(session_dir.rglob('*'))
                    if not any(f.is_file() for f in remaining):
                        shutil.rmtree(str(session_dir))
                except Exception:
                    pass

            job.cleaned_up = True
            job.save(update_fields=['cleaned_up'])
            cleaned_count += 1

        except Exception:
            logger.exception(f'Erro ao limpar SplitJob #{job.pk}')

    if cleaned_count > 0:
        logger.info(f'Limpeza: {cleaned_count} job(s) removido(s)')
