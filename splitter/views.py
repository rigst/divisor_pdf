"""
Views do app Splitter.
Endpoints para upload, consulta de status e download de PDFs divididos.
"""

import json
import logging
from pathlib import Path

from django.conf import settings
from django.utils.text import get_valid_filename
from django.http import (
    FileResponse,
    JsonResponse,
    HttpResponseNotAllowed,
    HttpResponseBadRequest,
    HttpResponseNotFound,
)
from django.shortcuts import render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

logger = logging.getLogger(__name__)


def _safe_pdf_filename(filename: str, used_names: set[str]) -> str:
    """Normaliza o nome do upload e evita sobrescrita dentro do mesmo job."""
    raw_name = Path(filename).name or 'arquivo.pdf'
    safe_name = get_valid_filename(raw_name)

    stem = Path(safe_name).stem or 'arquivo'
    suffix = Path(safe_name).suffix.lower()
    if suffix != '.pdf':
        safe_name = f'{stem}.pdf'

    candidate = safe_name
    counter = 2
    while candidate in used_names:
        candidate = f'{stem}_{counter}.pdf'
        counter += 1

    used_names.add(candidate)
    return candidate


def index(request):
    """Renderiza a página principal do aplicativo."""
    context = {
        'max_upload_size_mb': settings.MAX_UPLOAD_SIZE_MB,
        'max_total_upload_mb': settings.MAX_TOTAL_UPLOAD_MB,
    }
    return render(request, 'splitter/index.html', context)


@csrf_protect
@require_POST
def upload(request):
    """
    Recebe PDFs, nível de compressão Ghostscript e tamanho máximo de divisão.
    Cria um SplitJob e enfileira o processamento.

    Espera:
        - files: Um ou mais arquivos PDF (multipart/form-data)
        - compress_level: none | low | medium | high (form field)
        - should_split: true | false (form field)
        - max_size_mb: Tamanho máximo em MB (form field, opcional se should_split for false)

    Retorna:
        - 202: {job_id, task_id, message}
        - 400: {error} em caso de validação falha
    """
    from .models import SplitJob
    from .tasks import process_split_job

    # Validar nível de compressão
    compress_level = request.POST.get('compress_level', 'none').lower()
    if compress_level not in SplitJob.CompressLevel.values:
        return JsonResponse({'error': 'Nível de compressão inválido.'}, status=400)

    # Validar se deve dividir
    should_split_raw = request.POST.get('should_split', 'true').lower()
    should_split = should_split_raw in ('true', '1', 'on')

    # Se não for comprimir e nem dividir, não há nada a fazer
    if compress_level == SplitJob.CompressLevel.NONE and not should_split:
        return JsonResponse(
            {'error': 'Selecione pelo menos uma ação: Comprimir ou Dividir PDF.'},
            status=400
        )

    # Validar tamanho máximo de divisão se estiver habilitado
    max_size_mb = None
    if should_split:
        max_size_mb_raw = request.POST.get('max_size_mb')
        if not max_size_mb_raw:
            return JsonResponse({'error': 'Informe o tamanho máximo em MB para divisão.'}, status=400)

        try:
            max_size_mb = float(max_size_mb_raw)
            if max_size_mb < 0.1:
                return JsonResponse(
                    {'error': 'O tamanho máximo deve ser de pelo menos 0.1 MB.'},
                    status=400
                )
            if max_size_mb > settings.MAX_UPLOAD_SIZE_MB:
                return JsonResponse(
                    {'error': f'O tamanho máximo não pode exceder {settings.MAX_UPLOAD_SIZE_MB} MB.'},
                    status=400
                )
        except (ValueError, TypeError):
            return JsonResponse(
                {'error': 'Valor inválido para tamanho máximo.'},
                status=400
            )

    # Validar arquivos
    files = request.FILES.getlist('files')
    if not files:
        return JsonResponse(
            {'error': 'Envie pelo menos um arquivo PDF.'},
            status=400
        )

    # Validar cada arquivo
    total_size = 0
    filenames = []
    upload_files = []
    used_filenames = set()
    for f in files:
        # Verificar extensão
        if not f.name.lower().endswith('.pdf'):
            return JsonResponse(
                {'error': f'O arquivo "{f.name}" não é um PDF.'},
                status=400
            )

        # Verificar magic bytes do PDF
        header = f.read(5)
        f.seek(0)
        if header != b'%PDF-':
            return JsonResponse(
                {'error': f'O arquivo "{f.name}" não é um PDF válido.'},
                status=400
            )

        # Verificar tamanho individual
        if f.size > settings.MAX_UPLOAD_SIZE:
            size_mb = f.size / (1024 * 1024)
            return JsonResponse(
                {
                    'error': (
                        f'O arquivo "{f.name}" ({size_mb:.1f} MB) excede '
                        f'o limite de {settings.MAX_UPLOAD_SIZE_MB} MB.'
                    )
                },
                status=400
            )

        total_size += f.size
        safe_name = _safe_pdf_filename(f.name, used_filenames)
        filenames.append(safe_name)
        upload_files.append((f, safe_name))

    # Verificar tamanho total
    if total_size > settings.MAX_TOTAL_UPLOAD_SIZE:
        total_mb = total_size / (1024 * 1024)
        return JsonResponse(
            {
                'error': (
                    f'O tamanho total ({total_mb:.1f} MB) excede '
                    f'o limite de {settings.MAX_TOTAL_UPLOAD_MB} MB.'
                )
            },
            status=400
        )

    # Criar a sessão se não existir
    if not request.session.session_key:
        request.session.create()

    session_key = request.session.session_key

    try:
        # Criar o SplitJob
        job = SplitJob.objects.create(
            session_key=session_key,
            original_filenames=filenames,
            total_input_size_mb=round(total_size / (1024 * 1024), 2),
            compress_level=compress_level,
            should_split=should_split,
            max_size_mb=max_size_mb,
        )

        # Salvar os arquivos originais em disco
        input_dir = job.input_dir
        input_dir.mkdir(parents=True, exist_ok=True)

        for f, safe_name in upload_files:
            file_path = input_dir / safe_name
            with open(file_path, 'wb') as dest:
                for chunk in f.chunks():
                    dest.write(chunk)

        # Enfileirar processamento no Celery
        if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
            import threading
            from django.db import connection

            task_id = f'eager-{job.pk}'

            def run_async_eager():
                try:
                    # Garante conexões limpas em nova thread
                    connection.close()
                    process_split_job(job.pk)
                finally:
                    connection.close()

            # Executa em thread separada de background para liberar a requisição HTTP imediatamente
            thread = threading.Thread(target=run_async_eager)
            thread.daemon = True
            thread.start()
        else:
            task = process_split_job.delay(job.pk)
            task_id = task.id

        job.task_id = task_id
        job.save(update_fields=['task_id'])

        logger.info(
            f'SplitJob #{job.pk} criado: {len(filenames)} arquivo(s), '
            f'{job.total_input_size_mb} MB total, compress={compress_level}, split={should_split}'
        )

        return JsonResponse(
            {
                'job_id': job.pk,
                'task_id': task_id,
                'message': 'Upload realizado com sucesso. Processando...',
            },
            status=202
        )

    except Exception as exc:
        logger.exception('Erro inesperado no upload/processamento')
        return JsonResponse(
            {'error': f'Erro interno do servidor: {str(exc)}'},
            status=500
        )


@require_GET
def status(request, job_id):
    """
    Retorna o status atual de um SplitJob.

    Retorna JSON com:
        - status: pending | processing | completed | failed
        - progress: 0-100
        - total_output_files: (quando concluído)
        - total_output_size_mb: (quando concluído)
        - download_url: (quando concluído)
        - error_message: (quando falhou)
    """
    from .models import SplitJob

    try:
        job = SplitJob.objects.get(pk=job_id)
    except SplitJob.DoesNotExist:
        return JsonResponse({'error': 'Job não encontrado.'}, status=404)

    # Verificar que o job pertence à sessão atual
    if job.session_key != request.session.session_key:
        return JsonResponse({'error': 'Job não encontrado.'}, status=404)

    data = {
        'status': job.status,
        'progress': job.progress,
        'warnings': job.processing_warnings,
    }

    if job.status == SplitJob.Status.COMPLETED:
        data['total_output_files'] = job.total_output_files
        data['download_url'] = f'/api/download/{job.pk}/'
        
        # Define o tamanho total do ZIP resultante
        if job.total_output_size_mb:
            data['zip_size_mb'] = job.total_output_size_mb
        elif job.output_zip_path:
            # Fallback seguro caso o tamanho não esteja salvo no model
            zip_path = Path(job.output_zip_path)
            if zip_path.exists():
                data['zip_size_mb'] = round(zip_path.stat().st_size / (1024 * 1024), 2)
        else:
            data['zip_size_mb'] = 0.0

    elif job.status == SplitJob.Status.FAILED:
        data['error_message'] = job.error_message

    return JsonResponse(data)


@require_GET
def download(request, job_id):
    """
    Serve o arquivo ZIP resultante para download.

    Verifica se o job pertence à sessão atual e se está concluído.
    Usa FileResponse com streaming para eficiência.
    """
    from .models import SplitJob

    try:
        job = SplitJob.objects.get(pk=job_id)
    except SplitJob.DoesNotExist:
        return HttpResponseNotFound('Job não encontrado.')

    # Verificar sessão
    if job.session_key != request.session.session_key:
        return HttpResponseNotFound('Job não encontrado.')

    # Verificar status
    if job.status != SplitJob.Status.COMPLETED:
        return HttpResponseBadRequest('O processamento ainda não foi concluído.')

    # Verificar arquivo
    file_path = Path(job.output_zip_path)
    if not file_path.exists():
        return HttpResponseNotFound(
            'O arquivo já foi removido. Os arquivos são mantidos por apenas 1 hora.'
        )

    is_zip = file_path.suffix.lower() == '.zip'
    content_type = 'application/zip' if is_zip else 'application/pdf'

    if is_zip:
        if len(job.original_filenames) == 1:
            download_name = f'{Path(job.original_filenames[0]).stem}_dividido.zip'
        else:
            download_name = 'pdfs_divididos.zip'
    else:
        download_name = file_path.name

    return FileResponse(
        open(file_path, 'rb'),
        as_attachment=True,
        filename=download_name,
        content_type=content_type
    )
