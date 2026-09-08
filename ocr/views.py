"""
Views do app OCR.
Endpoints para upload, consulta de status e download do resultado
(PDF pesquisável ou Markdown).
"""

import logging
from pathlib import Path

from django.conf import settings
from django.db import models
from django.http import (
    FileResponse,
    HttpResponseBadRequest,
    HttpResponseNotFound,
    JsonResponse,
)
from django.shortcuts import render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

# Normalização do nome do upload: a regra é a mesma do divisor, e duplicá-la
# significaria manter duas versões da mesma defesa contra path traversal.
from splitter.views import _safe_pdf_filename

logger = logging.getLogger(__name__)


@require_GET
def index(request):
    """Renderiza a página de OCR."""
    from legal.forms import AceiteForm
    from legal.services import aceite_anonimo_valido

    from .models import OCRJob
    from .services import ocr_disponivel

    ja_aceitou = aceite_anonimo_valido(request)
    context = {
        "max_upload_size_mb": settings.MAX_UPLOAD_SIZE_MB,
        "max_total_upload_mb": settings.MAX_TOTAL_UPLOAD_MB,
        "idiomas": OCRJob.Idioma.choices,
        "ocr_disponivel": ocr_disponivel(),
        "precisa_aceite": not ja_aceitou,
        "form_aceite": None if ja_aceitou else AceiteForm(),
    }
    return render(request, "ocr/index.html", context)


@csrf_protect
@require_POST
def upload(request):
    """
    Recebe PDFs e enfileira o reconhecimento de texto.

    Espera:
        - files: um ou mais arquivos PDF (multipart/form-data)
        - idioma: por | eng | por+eng (form field)
        - forcar_ocr: true | false (form field)

    Retorna:
        - 202: {job_id, task_id, message}
        - 400: {error} em caso de validação falha
        - 503: {error} se o OCR não estiver disponível no servidor
    """
    from legal.models import OrigemAceite
    from legal.services import aceite_anonimo_valido, registrar_aceite

    from .models import OCRJob
    from .services import ocr_disponivel
    from .tasks import process_ocr_job

    if not ocr_disponivel():
        return JsonResponse(
            {
                "error": (
                    "O OCR está temporariamente indisponível neste servidor. "
                    "Tente novamente mais tarde."
                )
            },
            status=503,
        )

    # Mesma regra do divisor: quem recusa o envio sem aceite é o servidor.
    if not aceite_anonimo_valido(request):
        if request.POST.get("aceite_legal") not in ("true", "1", "on"):
            return JsonResponse(
                {
                    "error": "É preciso aceitar os Termos de Uso e a Política de Privacidade para enviar arquivos."
                },
                status=400,
            )
        registrar_aceite(request, origem=OrigemAceite.UPLOAD_ANONIMO)

    idioma = request.POST.get("idioma", OCRJob.Idioma.POR)
    if idioma not in OCRJob.Idioma.values:
        return JsonResponse({"error": "Idioma inválido para o OCR."}, status=400)

    forcar_ocr = request.POST.get("forcar_ocr", "false").lower() in ("true", "1", "on")

    files = request.FILES.getlist("files")
    if not files:
        return JsonResponse({"error": "Envie pelo menos um arquivo PDF."}, status=400)

    total_size = 0
    filenames = []
    upload_files = []
    used_filenames: set[str] = set()
    for f in files:
        if not f.name.lower().endswith(".pdf"):
            return JsonResponse({"error": f'O arquivo "{f.name}" não é um PDF.'}, status=400)

        header = f.read(5)
        f.seek(0)
        if header != b"%PDF-":
            return JsonResponse({"error": f'O arquivo "{f.name}" não é um PDF válido.'}, status=400)

        if f.size > settings.MAX_UPLOAD_SIZE:
            size_mb = f.size / (1024 * 1024)
            return JsonResponse(
                {
                    "error": (
                        f'O arquivo "{f.name}" ({size_mb:.1f} MB) excede '
                        f"o limite de {settings.MAX_UPLOAD_SIZE_MB} MB."
                    )
                },
                status=400,
            )

        total_size += f.size
        safe_name = _safe_pdf_filename(f.name, used_filenames)
        filenames.append(safe_name)
        upload_files.append((f, safe_name))

    if total_size > settings.MAX_TOTAL_UPLOAD_SIZE:
        total_mb = total_size / (1024 * 1024)
        return JsonResponse(
            {
                "error": (
                    f"O tamanho total ({total_mb:.1f} MB) excede "
                    f"o limite de {settings.MAX_TOTAL_UPLOAD_MB} MB."
                )
            },
            status=400,
        )

    if not request.session.session_key:
        request.session.create()

    session_key = request.session.session_key

    uso_ativo_mb = (
        OCRJob.objects.filter(session_key=session_key, cleaned_up=False)
        .exclude(status=OCRJob.Status.FAILED)
        .aggregate(total=models.Sum("total_input_size_mb"))["total"]
        or 0
    )
    projetado_mb = uso_ativo_mb + (total_size / (1024 * 1024))
    if projetado_mb > settings.MAX_TOTAL_UPLOAD_MB:
        return JsonResponse(
            {
                "error": (
                    f"O uso acumulado desta sessão ({projetado_mb:.1f} MB) excede "
                    f"o limite de {settings.MAX_TOTAL_UPLOAD_MB} MB. Aguarde a limpeza "
                    "automática dos arquivos antigos ou inicie uma nova sessão."
                )
            },
            status=400,
        )

    try:
        job = OCRJob.objects.create(
            session_key=session_key,
            original_filenames=filenames,
            total_input_size_mb=round(total_size / (1024 * 1024), 2),
            idioma=idioma,
            forcar_ocr=forcar_ocr,
        )

        input_dir = job.input_dir
        input_dir.mkdir(parents=True, exist_ok=True)

        for f, safe_name in upload_files:
            with open(input_dir / safe_name, "wb") as dest:
                for chunk in f.chunks():
                    dest.write(chunk)

        # Em modo eager o processamento roda em thread separada, para a
        # requisição HTTP voltar na hora — igual ao app `splitter`.
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            import threading

            from django.db import connection

            task_id = f"eager-ocr-{job.pk}"

            def run_async_eager():
                try:
                    connection.close()
                    process_ocr_job(job.pk)
                finally:
                    connection.close()

            thread = threading.Thread(target=run_async_eager)
            thread.daemon = True
            thread.start()
        else:
            task = process_ocr_job.delay(job.pk)
            task_id = task.id

        job.task_id = task_id
        job.save(update_fields=["task_id"])

        logger.info(
            f"OCRJob #{job.pk} criado: {len(filenames)} arquivo(s), "
            f"{job.total_input_size_mb} MB, idioma={idioma}, forçar={forcar_ocr}"
        )

        return JsonResponse(
            {
                "job_id": job.pk,
                "task_id": task_id,
                "message": "Upload realizado com sucesso. Reconhecendo o texto...",
            },
            status=202,
        )

    except Exception as exc:
        logger.exception("Erro inesperado no upload/OCR")
        return JsonResponse({"error": f"Erro interno do servidor: {exc!s}"}, status=500)


@require_GET
def status(request, job_id):
    """
    Status atual de um OCRJob.

    Retorna JSON com status, progresso, tamanhos por formato e as URLs de
    download de cada formato quando concluído.
    """
    from .models import OCRJob

    try:
        job = OCRJob.objects.get(pk=job_id)
    except OCRJob.DoesNotExist:
        return JsonResponse({"error": "Job não encontrado."}, status=404)

    if job.session_key != request.session.session_key:
        return JsonResponse({"error": "Job não encontrado."}, status=404)

    data = {
        "status": job.status,
        "progress": job.progress,
        "warnings": job.processing_warnings,
    }

    if job.status == OCRJob.Status.COMPLETED:
        data["total_output_files"] = job.total_output_files
        data["total_caracteres"] = job.total_caracteres or 0
        data["pdf_size_mb"] = job.output_pdf_size_mb or 0.0
        data["md_size_mb"] = job.output_md_size_mb or 0.0
        data["download_urls"] = {
            "pdf": f"/ocr/api/download/{job.pk}/pdf/",
            "md": f"/ocr/api/download/{job.pk}/md/",
        }
    elif job.status == OCRJob.Status.FAILED:
        data["error_message"] = job.error_message

    return JsonResponse(data)


@require_GET
def download(request, job_id, formato):
    """
    Serve o resultado no formato pedido: `pdf` (pesquisável) ou `md`.

    Confere sessão e status antes de abrir o arquivo; quando o envio tinha mais
    de um PDF, o que sai é um ZIP com todos os arquivos daquele formato.
    """
    from .models import OCRJob

    if formato not in OCRJob.Formato.values:
        return HttpResponseBadRequest("Formato inválido. Use 'pdf' ou 'md'.")

    try:
        job = OCRJob.objects.get(pk=job_id)
    except OCRJob.DoesNotExist:
        return HttpResponseNotFound("Job não encontrado.")

    if job.session_key != request.session.session_key:
        return HttpResponseNotFound("Job não encontrado.")

    if job.status != OCRJob.Status.COMPLETED:
        return HttpResponseBadRequest("O processamento ainda não foi concluído.")

    caminho = job.caminho_do_formato(formato)
    if not caminho:
        return HttpResponseNotFound("Resultado indisponível para este formato.")

    file_path = Path(caminho)
    if not file_path.exists():
        return HttpResponseNotFound(
            "O arquivo já foi removido. Os arquivos são mantidos por apenas 1 hora."
        )

    if file_path.suffix.lower() == ".zip":
        content_type = "application/zip"
        download_name = f"ocr_{formato}.zip"
    elif formato == OCRJob.Formato.PDF:
        content_type = "application/pdf"
        download_name = file_path.name
    else:
        content_type = "text/markdown; charset=utf-8"
        download_name = file_path.name

    return FileResponse(
        open(file_path, "rb"), as_attachment=True, filename=download_name, content_type=content_type
    )
