"""
Serviço de OCR.

O reconhecimento é feito pelo OCRmyPDF (MPL-2.0), invocado como processo
externo — mesmo arranjo já usado com o Ghostscript no app `splitter`. Ele
monta a camada de texto sobre a imagem original, então o PDF continua igual
aos olhos e passa a ser pesquisável.

O Markdown sai do próprio PDF reconhecido, lido com pypdf: assim o texto que
vira `.md` é exatamente o que ficou no PDF entregue, sem uma segunda extração
que pudesse divergir dele.

Nem todo PDF cabe no modo padrão (`--skip-text`). Dois casos comuns fazem o
reconhecimento sair vazio ou pela metade, e os dois são tratados aqui sem
exigir nada do usuário:

* PDF marcado como *Tagged PDF* — exportado do Word ou do PowerPoint. O
  OCRmyPDF aborta antes de começar (código 6) dizendo que o arquivo não
  precisa de OCR.
* PDF híbrido — cada página tem um pouco de texto real (título, marcadores)
  e o corpo em imagem. Aí `--skip-text` pula a página inteira por causa do
  pouco texto que ela já tem, e o corpo nunca é reconhecido. É o que a
  exportação de slides do PowerPoint produz: cada linha de bullet vira uma
  máscara de imagem.

Nos dois casos a saída é refeita com `--force-ocr`, que rasteriza a página e
regrava a camada de texto do zero. Como rasterizar infla o arquivo, o
resultado forçado passa por um Ghostscript de reamostragem quando fica maior
que a entrada.
"""

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Binário do OCRmyPDF. Configurável porque em algumas máquinas ele vem de
# pipx/venv e não do PATH do serviço.
OCR_BINARY = getattr(settings, "OCRMYPDF_BINARY", "ocrmypdf")

# Código de saída do OCRmyPDF para "este PDF já tem texto": vale tanto para o
# PriorOcrFoundError quanto para o TaggedPDFError do arquivo vindo do Office.
CODIGO_JA_TEM_TEXTO = 6

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


class _FalhaOCR(Exception):
    """Falha do processo do OCRmyPDF, com o código de saída preservado.

    Interna ao módulo: existe para que `run` decida entre repetir em outro
    modo e desistir. Para fora sai sempre um RuntimeError com a mensagem.
    """

    def __init__(self, codigo: int, mensagem: str, origem: Exception):
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
        self.origem = origem


def _imagens_da_pagina(pagina) -> int:
    """Quantos objetos de imagem a página referencia."""
    try:
        recursos = pagina.get("/Resources")
        if recursos is None:
            return 0
        xobjects = recursos.get_object().get("/XObject")
        if xobjects is None:
            return 0
        xobjects = xobjects.get_object()
        return sum(
            1 for nome in xobjects if xobjects[nome].get_object().get("/Subtype") == "/Image"
        )
    except Exception:
        # Página com estrutura fora do padrão não deve derrubar o diagnóstico:
        # sem informação, ela simplesmente não conta como suspeita.
        return 0


def texto_esparso(pdf_path: str | Path) -> bool:
    """
    O PDF saiu com texto de menos para o tanto de imagem que ele tem?

    É o sinal do arquivo híbrido: página com imagem e quase nenhum texto
    depois de um `--skip-text`, porque o pouco de texto nativo fez o OCRmyPDF
    pular a página inteira. Uma página em branco de verdade não conta — ela
    não tem imagem nenhuma.
    """
    from pypdf import PdfReader

    limiar = getattr(settings, "OCR_MIN_CHARS_POR_PAGINA", 150)
    proporcao = getattr(settings, "OCR_PROPORCAO_PAGINAS_ESPARSAS", 0.3)

    try:
        reader = PdfReader(str(pdf_path))
        total = len(reader.pages)
        if total == 0:
            return False

        suspeitas = 0
        for pagina in reader.pages:
            try:
                texto = pagina.extract_text() or ""
            except Exception:
                texto = ""
            if len(texto.strip()) < limiar and _imagens_da_pagina(pagina) > 0:
                suspeitas += 1
    except Exception:
        logger.warning(f"Não foi possível diagnosticar o texto de {pdf_path}.")
        return False

    if suspeitas == 0:
        return False

    logger.info(
        f"{suspeitas} de {total} página(s) de {Path(pdf_path).name} têm imagem "
        f"e menos de {limiar} caractere(s)."
    )
    return (suspeitas / total) >= proporcao


def _reamostrar_com_ghostscript(pdf_path: Path) -> None:
    """
    Reduz um PDF rasterizado, no lugar, reamostrando as imagens.

    Só faz sentido depois de um `--force-ocr`: a página virou imagem na
    resolução original do documento, e isso multiplica o tamanho do arquivo
    sem ganho de legibilidade. Falhar aqui não é erro — o PDF grande já está
    correto e é ele que fica.
    """
    dpi = getattr(settings, "OCR_RASTER_DPI", 150)
    timeout = getattr(settings, "GHOSTSCRIPT_TIMEOUT_SECONDS", 300)

    if shutil.which("gs") is None:
        logger.warning("Ghostscript não encontrado: o PDF rasterizado fica no tamanho original.")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        reduzido = Path(tmpdir) / "reduzido.pdf"
        cmd = [
            "gs",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.5",
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            "-dDetectDuplicateImages=true",
            "-dDownsampleColorImages=true",
            "-dColorImageDownsampleType=/Bicubic",
            f"-dColorImageResolution={dpi}",
            "-dDownsampleGrayImages=true",
            "-dGrayImageDownsampleType=/Bicubic",
            f"-dGrayImageResolution={dpi}",
            "-dDownsampleMonoImages=true",
            "-dMonoImageDownsampleType=/Subsample",
            f"-dMonoImageResolution={dpi * 2}",
            f"-sOutputFile={reduzido}",
            str(pdf_path),
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
        except Exception:
            logger.warning(
                f"Não foi possível reamostrar {pdf_path.name}; fica o PDF em tamanho cheio."
            )
            return

        if not reduzido.exists() or reduzido.stat().st_size == 0:
            return

        antes = pdf_path.stat().st_size
        depois = reduzido.stat().st_size
        if depois >= antes:
            return

        shutil.copyfile(str(reduzido), str(pdf_path))
        logger.info(
            f"{pdf_path.name} reamostrado a {dpi} dpi: "
            f"{antes / (1024 * 1024):.1f} MB -> {depois / (1024 * 1024):.1f} MB."
        )


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

    def montar_comando(
        self, input_path: Path, output_path: Path, forcar: bool | None = None
    ) -> list[str]:
        """Monta a linha de comando do OCRmyPDF.

        `forcar` sobrepõe o modo do processador: é como a segunda tentativa
        pede `--force-ocr` sem que a escolha do usuário mude.
        """
        forcar = self.forcar if forcar is None else forcar
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
        # modo que resolve PDF com texto ruim de OCR anterior, PDF marcado como
        # Tagged PDF e PDF híbrido. `--skip-text` preserva o texto nativo de
        # quem já tem, que é o caso comum.
        cmd.append("--force-ocr" if forcar else "--skip-text")
        cmd.extend([str(input_path), str(output_path)])
        return cmd

    def _executar(self, cmd: list[str]) -> None:
        """Roda o OCRmyPDF uma vez. Levanta `_FalhaOCR` se ele não terminar bem."""
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
            logger.warning(f"Falha no OCRmyPDF (código {e.returncode}): {bruto}")
            raise _FalhaOCR(
                e.returncode, detalhe or f"Falha no OCR (código {e.returncode}).", e
            ) from e
        except subprocess.TimeoutExpired as e:
            logger.exception(f"OCRmyPDF excedeu o timeout de {settings.OCR_TIMEOUT_SECONDS}s")
            raise RuntimeError(
                f"O OCR excedeu o tempo limite de {settings.OCR_TIMEOUT_SECONDS}s. "
                "Tente enviar um arquivo menor ou dividi-lo antes."
            ) from e
        except OSError as e:
            logger.exception("Erro inesperado ao invocar o OCRmyPDF.")
            raise RuntimeError(f"Erro ao invocar o OCR: {e}") from e

    def _validar_saida(self, output_path: Path) -> None:
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("O OCR não gerou o PDF de saída ou o arquivo saiu vazio.")

    def run(self, input_path: str | Path, output_path: str | Path) -> bool:
        """
        Gera o PDF pesquisável a partir do PDF de entrada.

        No modo padrão o texto nativo é preservado, mas se o OCRmyPDF recusar o
        arquivo por já ter texto, ou se a saída ficar com texto de menos para o
        tanto de imagem que ela tem, o reconhecimento é refeito com
        `--force-ocr`. Ver o cabeçalho do módulo para os dois casos.

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

        logger.info(
            f'Reconhecendo "{input_path.name}" com OCRmyPDF '
            f"(idioma={self.idioma}, forçar={self.forcar})..."
        )

        forcado = self.forcar
        try:
            self._executar(self.montar_comando(input_path, output_path, forcar=forcado))
        except _FalhaOCR as falha:
            if forcado or falha.codigo != CODIGO_JA_TEM_TEXTO:
                raise RuntimeError(falha.mensagem) from falha.origem
            # Recusado por já ter texto (inclui o Tagged PDF de arquivo vindo do
            # Office): rasterizar é a única saída, e é o que o usuário quis ao
            # mandar o arquivo para o OCR.
            logger.info(
                f'"{input_path.name}" foi recusado por já conter texto; refazendo com --force-ocr.'
            )
            forcado = True
            try:
                self._executar(self.montar_comando(input_path, output_path, forcar=True))
            except _FalhaOCR as segunda:
                raise RuntimeError(segunda.mensagem) from segunda.origem

        self._validar_saida(output_path)

        if not forcado and texto_esparso(output_path):
            logger.info(
                f'"{input_path.name}" saiu com texto esparso sobre as imagens '
                "(PDF híbrido); refazendo com --force-ocr."
            )
            if self._refazer_forcando(input_path, output_path):
                forcado = True

        if forcado:
            _reamostrar_com_ghostscript(output_path)

        logger.info(f'OCR concluído para "{input_path.name}".')
        return True

    def _refazer_forcando(self, input_path: Path, output_path: Path) -> bool:
        """
        Refaz o reconhecimento com `--force-ocr` sobre um resultado já válido.

        A segunda passada escreve em arquivo separado e só substitui o primeiro
        resultado se de fato reconhecer mais texto: sem isso, um arquivo em que
        a heurística errou sairia pior do que entrou.

        Returns:
            True se a saída forçada substituiu a anterior.
        """
        forcada = output_path.with_name(output_path.stem + "_forcado.pdf")
        try:
            try:
                self._executar(self.montar_comando(input_path, forcada, forcar=True))
            except (_FalhaOCR, RuntimeError) as exc:
                # O primeiro resultado continua valendo: pior um PDF com texto
                # parcial do que nenhum.
                logger.warning(f"A segunda passada com --force-ocr falhou: {exc}")
                return False

            if not forcada.exists() or forcada.stat().st_size == 0:
                return False

            antes = sum(len(p.strip()) for p in extrair_paginas(output_path))
            depois = sum(len(p.strip()) for p in extrair_paginas(forcada))
            if depois <= antes:
                logger.info(
                    f"A passada forçada não reconheceu mais texto "
                    f"({depois} contra {antes} caracteres); mantido o primeiro resultado."
                )
                return False

            forcada.replace(output_path)
            logger.info(f"Texto reconhecido subiu de {antes} para {depois} caractere(s).")
            return True
        finally:
            forcada.unlink(missing_ok=True)


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


# Marcadores de lista que o OCR costuma devolver como caractere solto. Os
# quatro últimos aparecem quando o Tesseract lê o símbolo gráfico de bullet de
# um slide rasterizado. Letras soltas ficam de fora de propósito: "e" e "o"
# começam frase em português e virariam item de lista sem ser.
_MARCADORES = re.compile(r"^\s*[•▪◦·–—*◼✓«»=]\s+")


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
