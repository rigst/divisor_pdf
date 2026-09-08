"""
Serviço de OCR.

O reconhecimento é feito pelo OCRmyPDF (MPL-2.0), invocado como processo
externo — mesmo arranjo já usado com o Ghostscript no app `splitter`. Ele
monta a camada de texto sobre a imagem original, então o PDF continua igual
aos olhos e passa a ser pesquisável.

O Markdown sai do próprio PDF reconhecido, lido com pypdf: assim o texto que
vira `.md` é exatamente o que ficou no PDF entregue, sem uma segunda extração
que pudesse divergir dele.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Binário do OCRmyPDF. Configurável porque em algumas máquinas ele vem de
# pipx/venv e não do PATH do serviço.
OCR_BINARY = getattr(settings, "OCRMYPDF_BINARY", "ocrmypdf")

# Mensagens para os códigos de saída que o usuário consegue agir a respeito.
# A lista completa está em https://ocrmypdf.readthedocs.io/en/latest/advanced.html
MENSAGENS_POR_CODIGO = {
    2: "Configuração inválida do OCR.",
    3: "Falta uma dependência do OCR no servidor (Tesseract ou Ghostscript).",
    4: "O PDF gerado pelo OCR saiu inválido.",
    5: "Não foi possível ler ou gravar o arquivo.",
    6: "O PDF já contém texto. Marque “refazer OCR” para reconhecer mesmo assim.",
    8: "O PDF está protegido por senha. Remova a proteção e envie de novo.",
    9: "O PDF está corrompido ou em um formato que o OCR não entende.",
    15: "Erro desconhecido durante o OCR.",
}


def ocr_disponivel() -> bool:
    """O binário do OCRmyPDF está instalado e acessível?

    A tela e a view consultam isto antes de aceitar upload: falhar no envio,
    com explicação, é melhor do que aceitar o arquivo e falhar 30s depois.
    """
    return shutil.which(OCR_BINARY) is not None


class OCRProcessor:
    """Roda o OCRmyPDF em um arquivo, com o idioma e o modo escolhidos."""

    def __init__(self, idioma: str = "por", forcar: bool = False):
        """
        Args:
            idioma: código(s) de idioma do Tesseract ('por', 'eng', 'por+eng').
            forcar: refaz o OCR mesmo em páginas que já têm texto.
        """
        self.idioma = idioma
        self.forcar = forcar

    def montar_comando(self, input_path: Path, output_path: Path) -> list[str]:
        """Monta a linha de comando do OCRmyPDF."""
        cmd = [
            OCR_BINARY,
            "--language",
            self.idioma,
            "--output-type",
            "pdf",
            "--quiet",
            # Corrige páginas viradas e inclinadas antes de reconhecer: é o que
            # mais melhora o resultado em documento digitalizado no scanner.
            "--rotate-pages",
            "--deskew",
            "--jobs",
            str(settings.OCR_JOBS),
            # Otimização leve. Níveis maiores dependem de jbig2/pngquant, que
            # nem sempre estão instalados, e o ganho não compensa a falha.
            "--optimize",
            "1",
        ]
        # `--force-ocr` rasteriza a página inteira e regrava por cima; é o único
        # modo que resolve PDF com texto ruim de OCR anterior. `--skip-text`
        # preserva o texto nativo de quem já tem, que é o caso comum.
        cmd.append("--force-ocr" if self.forcar else "--skip-text")
        cmd.extend([str(input_path), str(output_path)])
        return cmd

    def run(self, input_path: str | Path, output_path: str | Path) -> bool:
        """
        Gera o PDF pesquisável a partir do PDF de entrada.

        Returns:
            True se o OCR concluiu e o arquivo de saída tem conteúdo.

        Raises:
            FileNotFoundError: se o PDF de entrada não existir.
            RuntimeError: se o OCRmyPDF falhar, estourar o tempo ou não gerar saída.
        """
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not input_path.exists():
            raise FileNotFoundError(f"PDF de entrada não encontrado: {input_path}")

        if not ocr_disponivel():
            raise RuntimeError(
                "O OCR não está disponível neste servidor: o OCRmyPDF não foi encontrado."
            )

        cmd = self.montar_comando(input_path, output_path)
        logger.info(
            f'Reconhecendo "{input_path.name}" com OCRmyPDF '
            f"(idioma={self.idioma}, forçar={self.forcar})..."
        )

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=settings.OCR_TIMEOUT_SECONDS,
            )
        except subprocess.CalledProcessError as e:
            detalhe = MENSAGENS_POR_CODIGO.get(e.returncode)
            bruto = (e.stderr or e.stdout or "").strip()
            logger.exception(f"Falha no OCRmyPDF (código {e.returncode}): {bruto}")
            raise RuntimeError(detalhe or f"Falha no OCR (código {e.returncode}).") from e
        except subprocess.TimeoutExpired as e:
            logger.exception(
                f"OCRmyPDF excedeu o timeout de {settings.OCR_TIMEOUT_SECONDS}s"
            )
            raise RuntimeError(
                f"O OCR excedeu o tempo limite de {settings.OCR_TIMEOUT_SECONDS}s. "
                "Tente enviar um arquivo menor ou dividi-lo antes."
            ) from e
        except OSError as e:
            logger.exception("Erro inesperado ao invocar o OCRmyPDF.")
            raise RuntimeError(f"Erro ao invocar o OCR: {e}") from e

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("O OCR não gerou o PDF de saída ou o arquivo saiu vazio.")

        logger.info(f'OCR concluído para "{input_path.name}".')
        return True


def extrair_paginas(pdf_path: str | Path) -> list[str]:
    """Texto de cada página do PDF já reconhecido, na ordem do documento."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    paginas = []
    for numero, pagina in enumerate(reader.pages, start=1):
        try:
            paginas.append(pagina.extract_text() or "")
        except Exception:
            # Uma página ilegível não pode derrubar o documento inteiro: entra
            # vazia e o aviso aparece no `.md`.
            logger.warning(f"Não foi possível extrair o texto da página {numero} de {pdf_path}")
            paginas.append("")
    return paginas


# Marcadores de lista que o OCR costuma devolver como caractere solto.
_MARCADORES = re.compile(r"^\s*[•▪◦·–—*]\s+")


def _normalizar_linha(linha: str) -> str:
    """Uma linha de texto cru virando uma linha de Markdown."""
    linha = linha.rstrip()
    if _MARCADORES.match(linha):
        return "- " + _MARCADORES.sub("", linha)
    return linha


def texto_para_markdown(paginas: list[str], titulo: str) -> str:
    """
    Monta o `.md` a partir do texto das páginas.

    A conversão é deliberadamente conservadora: OCR não devolve estrutura, e
    inventar títulos e ênfases a partir de heurística estraga mais documento do
    que conserta. O que se faz aqui é o que dá para afirmar com certeza —
    separar as páginas, marcar listas e limpar espaço em branco repetido.
    """
    linhas = [f"# {titulo}", ""]

    for numero, texto in enumerate(paginas, start=1):
        linhas.append(f"## Página {numero}")
        linhas.append("")

        conteudo = (texto or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not conteudo:
            linhas.append("*(Nenhum texto reconhecido nesta página.)*")
            linhas.append("")
            continue

        vazias_seguidas = 0
        for linha_bruta in conteudo.split("\n"):
            linha = _normalizar_linha(linha_bruta)
            if not linha.strip():
                vazias_seguidas += 1
                # Uma linha em branco separa parágrafos; várias não significam
                # nada em Markdown e só incham o arquivo.
                if vazias_seguidas > 1:
                    continue
                linhas.append("")
                continue
            vazias_seguidas = 0
            linhas.append(linha)

        linhas.append("")

    return "\n".join(linhas).rstrip() + "\n"
