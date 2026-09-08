"""
Views do app OCR.
Endpoints para upload, consulta de status e download do resultado
(PDF pesquisável ou Markdown).
"""

import logging
from pathlib import Path

from django.conf import settings
from django.http import (
    FileResponse,
    HttpResponseBadRequest,
    HttpResponseNotFound,
    JsonResponse,
)
from django.shortcuts import render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

# As validações de upload são as mesmas do divisor e moram em um lugar só.
from core.uploads import (
    UploadInvalido,
    gravar_uploads,
    validar_pdfs,
    validar_uso_da_sessao,
)

logger = logging.getLogger(__name__)

JOB_NAO_ENCONTRADO = "Job não encontrado."


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

    if not request.session.session_key:
        request.session.create()
    session_key = request.session.session_key

    try:
        filenames, upload_files, total_size = validar_pdfs(request.FILES.getlist("files"))
        validar_uso_da_sessao(OCRJob, session_key, total_size)
    except UploadInvalido as recusa:
        return JsonResponse({"error": str(recusa)}, status=400)

    try:
        job = OCRJob.objects.create(
            session_key=session_key,
            original_filenames=filenames,
            total_input_size_mb=round(total_size / (1024 * 1024), 2),
            idioma=idioma,
            forcar_ocr=forcar_ocr,
        )
        gravar_uploads(upload_files, job.input_dir)
        job.task_id = _enfileirar(process_ocr_job, job.pk)
        job.save(update_fields=["task_id"])

        # Só valores de origem controlada vão para o log: o rótulo do idioma sai
        # das choices do model, não do que veio no formulário.
        logger.info(
            "OCRJob #%s criado: %s arquivo(s), %s MB, idioma=%s, forçar=%s",
            job.pk,
            len(filenames),
            job.total_input_size_mb,
            job.get_idioma_display(),
            "sim" if forcar_ocr else "não",
        )

        return JsonResponse(
            {
                "job_id": job.pk,
                "task_id": job.task_id,
                "message": "Upload realizado com sucesso. Reconhecendo o texto...",
            },
            status=202,
        )

    except Exception as exc:
        logger.exception("Erro inesperado no upload/OCR")
        return JsonResponse({"error": f"Erro interno do servidor: {exc!s}"}, status=500)


def _enfileirar(task, job_id: int) -> str:
    """Manda a task para o Celery e devolve o id do trabalho.

    Em modo eager o processamento roda numa thread separada, para a requisição
    HTTP voltar na hora em vez de segurar o navegador até o fim do OCR — igual
    ao app `splitter`.
    """
    if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return task.delay(job_id).id

    import threading

    from django.db import connection

    def executar():
        try:
            connection.close()
            task(job_id)
        finally:
            connection.close()

    thread = threading.Thread(target=executar)
    thread.daemon = True
    thread.start()
    return f"eager-ocr-{job_id}"


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
        return JsonResponse({"error": JOB_NAO_ENCONTRADO}, status=404)

    if job.session_key != request.session.session_key:
        return JsonResponse({"error": JOB_NAO_ENCONTRADO}, status=404)

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
        return HttpResponseNotFound(JOB_NAO_ENCONTRADO)

    if job.session_key != request.session.session_key:
        return HttpResponseNotFound(JOB_NAO_ENCONTRADO)

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
