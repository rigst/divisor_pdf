"""
Validação de uploads de PDF, compartilhada pelo divisor e pelo OCR.

As duas telas recebem os mesmos arquivos, com os mesmos limites e as mesmas
defesas — extensão, magic bytes, tamanho por arquivo, tamanho total e uso
acumulado da sessão. Manter duas cópias disso significaria manter duas versões
da mesma defesa, e é sempre a segunda que fica para trás.
"""

from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils.text import get_valid_filename


class UploadInvalido(Exception):
    """Recusa de upload com a mensagem que vai para o usuário.

    A view traduz isso em 400 com `{"error": ...}`; a mensagem é escrita para
    ser lida por quem enviou o arquivo, não por quem lê o log.
    """


def nome_pdf_seguro(filename: str, usados: set[str]) -> str:
    """Normaliza o nome do upload e evita sobrescrita dentro do mesmo job."""
    raw_name = Path(filename).name or "arquivo.pdf"
    safe_name = get_valid_filename(raw_name)

    stem = Path(safe_name).stem or "arquivo"
    suffix = Path(safe_name).suffix.lower()
    if suffix != ".pdf":
        safe_name = f"{stem}.pdf"

    candidate = safe_name
    counter = 2
    while candidate in usados:
        candidate = f"{stem}_{counter}.pdf"
        counter += 1

    usados.add(candidate)
    return candidate


def validar_pdfs(files) -> tuple[list[str], list[tuple], int]:
    """
    Confere a lista de arquivos enviados e devolve o que a view precisa gravar.

    Returns:
        (nomes_seguros, [(arquivo, nome_seguro), ...], tamanho_total_em_bytes)

    Raises:
        UploadInvalido: no primeiro arquivo que não passa.
    """
    if not files:
        raise UploadInvalido("Envie pelo menos um arquivo PDF.")

    total_size = 0
    nomes: list[str] = []
    arquivos: list[tuple] = []
    usados: set[str] = set()

    for f in files:
        if not f.name.lower().endswith(".pdf"):
            raise UploadInvalido(f'O arquivo "{f.name}" não é um PDF.')

        # Extensão é o que o usuário digitou; o cabeçalho é o que o arquivo é.
        header = f.read(5)
        f.seek(0)
        if header != b"%PDF-":
            raise UploadInvalido(f'O arquivo "{f.name}" não é um PDF válido.')

        if f.size > settings.MAX_UPLOAD_SIZE:
            size_mb = f.size / (1024 * 1024)
            raise UploadInvalido(
                f'O arquivo "{f.name}" ({size_mb:.1f} MB) excede '
                f"o limite de {settings.MAX_UPLOAD_SIZE_MB} MB."
            )

        total_size += f.size
        nome_seguro = nome_pdf_seguro(f.name, usados)
        nomes.append(nome_seguro)
        arquivos.append((f, nome_seguro))

    if total_size > settings.MAX_TOTAL_UPLOAD_SIZE:
        total_mb = total_size / (1024 * 1024)
        raise UploadInvalido(
            f"O tamanho total ({total_mb:.1f} MB) excede "
            f"o limite de {settings.MAX_TOTAL_UPLOAD_MB} MB."
        )

    return nomes, arquivos, total_size


def validar_uso_da_sessao(model, session_key: str, total_size: int) -> None:
    """
    Impede que uma sessão ocupe mais que o limite somando jobs ainda em disco.

    Sem isto o limite por envio seria contornável mandando vários envios
    seguidos, já que os arquivos ficam uma hora no servidor.

    Raises:
        UploadInvalido: se o envio levaria a sessão acima do teto.
    """
    uso_ativo_mb = (
        model.objects.filter(session_key=session_key, cleaned_up=False)
        .exclude(status=model.Status.FAILED)
        .aggregate(total=models.Sum("total_input_size_mb"))["total"]
        or 0
    )
    projetado_mb = uso_ativo_mb + (total_size / (1024 * 1024))
    if projetado_mb > settings.MAX_TOTAL_UPLOAD_MB:
        raise UploadInvalido(
            f"O uso acumulado desta sessão ({projetado_mb:.1f} MB) excede "
            f"o limite de {settings.MAX_TOTAL_UPLOAD_MB} MB. Aguarde a limpeza "
            "automática dos arquivos antigos ou inicie uma nova sessão."
        )


def gravar_uploads(arquivos: list[tuple], destino) -> None:
    """Grava os uploads no diretório de entrada do job, em blocos."""
    destino.mkdir(parents=True, exist_ok=True)
    for f, nome_seguro in arquivos:
        with open(destino / nome_seguro, "wb") as saida:
            for chunk in f.chunks():
                saida.write(chunk)
