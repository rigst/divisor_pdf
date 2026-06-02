"""
Serviço de divisão de PDFs.
Contém a lógica principal de dividir um PDF em partes menores
respeitando o limite de tamanho máximo, utilizando a biblioteca pypdf (MIT).
"""

import io
import logging
from pathlib import Path

from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)


class PDFSplitter:
    """
    Divide um arquivo PDF em múltiplos arquivos menores,
    garantindo que cada parte não exceda o tamanho máximo especificado.
    """

    def __init__(self, max_size_bytes: int):
        if max_size_bytes <= 0:
            raise ValueError('O tamanho máximo deve ser maior que zero.')
        self.max_size_bytes = max_size_bytes

    def split(self, input_path: str, output_dir: str, base_name: str = None) -> list[str]:
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
        last_valid_bytes = None
        last_valid_end = start_page

        for end_page in range(start_page + 1, total_pages + 1):
            pdf_bytes = self._render_pages(reader, start_page, end_page)

            if len(pdf_bytes) <= self.max_size_bytes:
                last_valid_bytes = pdf_bytes
                last_valid_end = end_page
            else:
                if last_valid_bytes is not None:
                    return last_valid_end, last_valid_bytes
                else:
                    logger.warning(
                        f'Página {start_page + 1} excede o limite de tamanho '
                        f'({len(pdf_bytes) / (1024*1024):.2f} MB). '
                        f'Incluindo-a em arquivo individual.'
                    )
                    return end_page, pdf_bytes

        if last_valid_bytes is not None:
            return last_valid_end, last_valid_bytes

        pdf_bytes = self._render_pages(reader, start_page, start_page + 1)
        return start_page + 1, pdf_bytes

    def _render_pages(
        self, reader: PdfReader, start_page: int, end_page: int
    ) -> bytes:
        writer = PdfWriter()
        for page_num in range(start_page, end_page):
            writer.add_page(reader.pages[page_num])

        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()


class PDFCompressor:
    """
    Comprime arquivos PDF utilizando o Ghostscript.
    Oferece três níveis de qualidade/tamanho: alta, média e baixa.
    """

    SETTINGS_MAP = {
        'low': '/printer',
        'medium': '/ebook',
        'high': '/screen',
    }

    def __init__(self, quality_level: str):
        if quality_level not in self.SETTINGS_MAP:
            raise ValueError(f'Nível de qualidade inválido: {quality_level}')
        self.quality_level = quality_level
        self.gs_setting = self.SETTINGS_MAP[quality_level]

    def compress(self, input_path: str, output_path: str) -> bool:
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
                check=True
            )
            
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
        except Exception as e:
            logger.exception('Erro inesperado durante a compressão.')
            raise RuntimeError(f'Erro ao invocar o compressor de PDF: {e}')
