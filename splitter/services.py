"""
Serviço de divisão de PDFs.
Contém a lógica principal de dividir um PDF em partes menores
respeitando o limite de tamanho máximo, utilizando a biblioteca pypdf (MIT).
"""

import io
import logging
from pathlib import Path

from django.conf import settings
from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)


class PDFSplitter:
    """
    Divide um arquivo PDF em múltiplos arquivos menores,
    garantindo que cada parte não exceda o tamanho máximo especificado.
    """

    def __init__(self, max_size_bytes: int, compress_level: str = 'none'):
        """
        Args:
            max_size_bytes: Tamanho máximo de cada arquivo PDF resultante em bytes.
            compress_level: Nível de compressão Ghostscript ('none', 'low', 'medium', 'high').
        """
        if max_size_bytes <= 0:
            raise ValueError('O tamanho máximo deve ser maior que zero.')
        self.max_size_bytes = max_size_bytes
        self.compress_level = compress_level

    def split(self, input_path: str, output_dir: str, base_name: str = None) -> list[str]:
        """
        Divide um PDF em partes menores.

        Args:
            input_path: Caminho do PDF original.
            output_dir: Diretório onde os PDFs divididos serão salvos.
            base_name: Nome base para os arquivos de saída (sem extensão).
                       Se None, usa o nome do arquivo original.

        Returns:
            Lista de caminhos dos PDFs gerados.

        Raises:
            FileNotFoundError: Se o arquivo de entrada não existir.
            ValueError: Se o arquivo não for um PDF válido.
        """
        input_path = Path(input_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if not input_path.exists():
            raise FileNotFoundError(f'Arquivo não encontrado: {input_path}')

        if base_name is None:
            base_name = input_path.stem

        try:
            reader = PdfReader(str(input_path))
            total_pages = len(reader.pages)
        except Exception as e:
            raise ValueError(f'Não foi possível abrir o PDF: {e}')

        if total_pages == 0:
            raise ValueError('O PDF não contém páginas.')

        logger.info(
            f'Dividindo "{input_path.name}" ({total_pages} páginas) '
            f'com limite de {self.max_size_bytes / (1024*1024):.1f} MB'
        )

        output_files = []
        part_number = 1
        current_start_page = 0

        while current_start_page < total_pages:
            result = self._find_optimal_split(
                reader, current_start_page, total_pages
            )
            end_page, pdf_bytes = result

            # Salvar o PDF da parte
            output_path = output_dir / f'{base_name}_parte{part_number:03d}.pdf'
            output_path.write_bytes(pdf_bytes)
            output_files.append(str(output_path))

            pages_in_part = end_page - current_start_page
            size_mb = len(pdf_bytes) / (1024 * 1024)
            logger.info(
                f'  Parte {part_number}: páginas {current_start_page + 1}-{end_page} '
                f'({pages_in_part} pág, {size_mb:.2f} MB)'
            )

            current_start_page = end_page
            part_number += 1

        logger.info(
            f'Divisão concluída: {len(output_files)} arquivo(s) gerado(s)'
        )

        return output_files

    def _find_optimal_split(
        self, reader: PdfReader, start_page: int, total_pages: int
    ) -> tuple[int, bytes]:
        """
        Encontra o ponto ótimo de divisão usando busca binária de alta performance.

        Args:
            reader: Instância do PdfReader do PDF de origem.
            start_page: Página inicial (0-indexed).
            total_pages: Total de páginas no documento.

        Returns:
            Tupla com (end_page, pdf_bytes) onde end_page é exclusivo.
        """
        low = start_page + 1
        high = total_pages
        optimal_end = start_page + 1
        optimal_bytes = None

        # Primeiro, testa a primeira página sozinha
        first_page_bytes = self._render_pages(reader, start_page, start_page + 1)
        if len(first_page_bytes) > self.max_size_bytes:
            # A primeira página já excede o limite. Ela deve ir sozinha.
            logger.warning(
                f'Página {start_page + 1} excede o limite de tamanho '
                f'({len(first_page_bytes) / (1024*1024):.2f} MB). '
                f'Incluindo-a em arquivo individual.'
            )
            return start_page + 1, first_page_bytes

        # Se a primeira página cabe, faz busca binária para encontrar o máximo de páginas que cabem
        optimal_bytes = first_page_bytes
        
        while low <= high:
            mid = (low + high) // 2
            
            # Se mid é a primeira página, já testamos e cabe
            if mid == start_page + 1:
                low = mid + 1
                continue
                
            pdf_bytes = self._render_pages(reader, start_page, mid)
            
            if len(pdf_bytes) <= self.max_size_bytes:
                # Cabe! Tenta incluir mais páginas (busca à direita)
                optimal_end = mid
                optimal_bytes = pdf_bytes
                low = mid + 1
            else:
                # Não cabe! Tenta menos páginas (busca à esquerda)
                high = mid - 1

        return optimal_end, optimal_bytes

    def _render_pages(
        self, reader: PdfReader, start_page: int, end_page: int
    ) -> bytes:
        """
        Renderiza um subconjunto de páginas em um PDF em memória,
        comprimindo-o com Ghostscript se a compressão estiver ativa.

        Args:
            reader: Instância de PdfReader.
            start_page: Página inicial (0-indexed, inclusive).
            end_page: Página final (0-indexed, exclusive).

        Returns:
            Bytes do PDF resultante.
        """
        writer = PdfWriter()
        for page_num in range(start_page, end_page):
            writer.add_page(reader.pages[page_num])

        try:
            writer.compress_identical_objects()
        except Exception:
            pass

        buffer = io.BytesIO()
        writer.write(buffer)
        raw_bytes = buffer.getvalue()

        if self.compress_level and self.compress_level != 'none':
            return self._compress_bytes(raw_bytes, self.compress_level)

        return raw_bytes

    def _compress_bytes(self, raw_bytes: bytes, compress_level: str) -> bytes:
        """
        Comprime bytes de PDF utilizando Ghostscript de forma segura em diretório temporário.
        """
        import tempfile
        import subprocess
        from pathlib import Path

        gs_setting = PDFCompressor.SETTINGS_MAP.get(compress_level, '/ebook')
        
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / 'input.pdf'
            output_path = Path(tmpdir) / 'output.pdf'
            
            input_path.write_bytes(raw_bytes)
            
            cmd = [
                'gs',
                '-sDEVICE=pdfwrite',
                '-dCompatibilityLevel=1.4',
                f'-dPDFSETTINGS={gs_setting}',
                '-dNOPAUSE',
                '-dQUIET',
                '-dBATCH',
                f'-sOutputFile={output_path}',
                str(input_path)
            ]
            
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=settings.GHOSTSCRIPT_TIMEOUT_SECONDS,
                )
                if output_path.exists() and output_path.stat().st_size > 0:
                    return output_path.read_bytes()
            except Exception as e:
                logger.warning(f'Erro na compressão intermediária via Ghostscript: {e}')
                
        return raw_bytes


class PDFCompressor:
    """
    Comprime arquivos PDF utilizando o Ghostscript.
    Oferece três níveis de qualidade/tamanho: alta, média e baixa.
    """

    # Mapeamento do nível de qualidade amigável para o perfil do Ghostscript
    # 'low' -> Menor compressão, melhor qualidade (impressão) -> /printer
    # 'medium' -> Compressão equilibrada (leitura digital) -> /ebook
    # 'high' -> Máxima compressão, menor qualidade (tela) -> /screen
    SETTINGS_MAP = {
        'low': '/printer',
        'medium': '/ebook',
        'high': '/screen',
    }

    def __init__(self, quality_level: str):
        """
        Args:
            quality_level: 'low', 'medium' ou 'high' (nível de compressão/qualidade).
        """
        if quality_level not in self.SETTINGS_MAP:
            raise ValueError(f'Nível de qualidade inválido: {quality_level}')
        self.quality_level = quality_level
        self.gs_setting = self.SETTINGS_MAP[quality_level]

    def compress(self, input_path: str, output_path: str) -> bool:
        """
        Executa a compressão via Ghostscript do arquivo de entrada para o de saída.

        Args:
            input_path: Caminho completo do PDF original.
            output_path: Caminho completo onde o PDF comprimido será salvo.

        Returns:
            True se a compressão foi bem sucedida.

        Raises:
            RuntimeError: Se o Ghostscript falhar ou retornar erro.
        """
        import subprocess

        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not input_path.exists():
            raise FileNotFoundError(f'PDF de entrada não encontrado: {input_path}')

        cmd = [
            'gs',
            '-sDEVICE=pdfwrite',
            '-dCompatibilityLevel=1.4',
            f'-dPDFSETTINGS={self.gs_setting}',
            '-dNOPAUSE',
            '-dQUIET',
            '-dBATCH',
            f'-sOutputFile={output_path}',
            str(input_path)
        ]

        logger.info(
            f'Comprimindo "{input_path.name}" para "{output_path.name}" '
            f'usando Ghostscript ({self.quality_level} / {self.gs_setting})...'
        )

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=settings.GHOSTSCRIPT_TIMEOUT_SECONDS,
            )
            
            # Validar se o arquivo de saída realmente foi criado e tem conteúdo
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError('Ghostscript não gerou o PDF de saída ou o arquivo está vazio.')

            old_size_mb = input_path.stat().st_size / (1024 * 1024)
            new_size_mb = output_path.stat().st_size / (1024 * 1024)
            reduction = (1 - (new_size_mb / old_size_mb)) * 100
            
            logger.info(
                f'Compressão concluída com sucesso! '
                f'{old_size_mb:.2f} MB -> {new_size_mb:.2f} MB '
                f'({reduction:.1f}% de redução)'
            )
            return True

        except subprocess.CalledProcessError as e:
            err_msg = e.stderr or e.stdout or 'Erro desconhecido.'
            logger.error(f'Falha no Ghostscript: {err_msg}')
            raise RuntimeError(f'Falha na compressão do PDF via Ghostscript: {err_msg}')
        except subprocess.TimeoutExpired as e:
            logger.error(f'Ghostscript excedeu o timeout de {settings.GHOSTSCRIPT_TIMEOUT_SECONDS}s')
            raise RuntimeError(
                f'Ghostscript excedeu o tempo limite de {settings.GHOSTSCRIPT_TIMEOUT_SECONDS}s'
            ) from e
        except Exception as e:
            logger.exception('Erro inesperado durante a compressão.')
            raise RuntimeError(f'Erro ao invocar o compressor de PDF: {e}')
