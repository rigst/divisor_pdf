import io
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from pypdf import PdfWriter, PdfReader

from .models import SplitJob
from .services import PDFSplitter, PDFCompressor
from .tasks import process_split_job, cleanup_expired_sessions
from .views import _safe_pdf_filename


# Diretório temporário específico para arquivos de teste
TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix='divisor_pdf_test_media_')


def create_dummy_pdf(num_pages=3):
    """
    Gera em memória um arquivo PDF válido com o número de páginas especificado.
    Retorna os bytes do PDF.
    """
    writer = PdfWriter()
    for i in range(num_pages):
        # Adiciona uma página vazia
        writer.add_blank_page(width=612, height=792)  # Tamanho Carta padrão

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class PDFServicesTestCase(TestCase):
    """Testa os serviços de negócio: PDFSplitter e PDFCompressor."""

    def setUp(self):
        self.input_pdf_bytes = create_dummy_pdf(num_pages=3)
        
        # Cria arquivos temporários de entrada e saída
        self.test_dir = os.path.join(TEMP_MEDIA_ROOT, 'services_tests')
        os.makedirs(self.test_dir, exist_ok=True)
        
        self.input_path = os.path.join(self.test_dir, 'input.pdf')
        with open(self.input_path, 'wb') as f:
            f.write(self.input_pdf_bytes)

    def tearDown(self):
        if os.path.exists(TEMP_MEDIA_ROOT):
            shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def test_split_happy_path(self):
        """Testa se a divisão ocorre com sucesso limitando o tamanho em bytes."""
        file_size = os.path.getsize(self.input_path)
        
        # Divide de forma que cada página fique em um arquivo separado
        max_size_bytes = int(file_size / 2.5)  
        splitter = PDFSplitter(max_size_bytes)
        
        output_dir = os.path.join(self.test_dir, 'output_happy')
        output_files = splitter.split(self.input_path, output_dir, base_name='test_part')
        
        # Deve gerar 3 arquivos (um para cada página)
        self.assertEqual(len(output_files), 3)
        for path in output_files:
            self.assertTrue(os.path.exists(path))
            reader = PdfReader(path)
            # Cada parte deve conter exatamente 1 página
            self.assertEqual(len(reader.pages), 1)

    def test_split_single_large_page_edge_case(self):
        """
        Caso de Borda: Uma única página excede o tamanho máximo solicitado.
        O splitter deve colocá-la sozinha no arquivo e continuar.
        """
        # Limite extremamente baixo que até uma única página excede
        max_size_bytes = 10  # 10 bytes
        splitter = PDFSplitter(max_size_bytes)
        
        output_dir = os.path.join(self.test_dir, 'output_large_page')
        output_files = splitter.split(self.input_path, output_dir, base_name='test_edge')
        
        # Deve gerar 3 arquivos individuais mesmo que cada um tenha excedido o limite de 10 bytes
        # (já que uma página de PDF física não pode ser dividida ao meio)
        self.assertEqual(len(output_files), 3)

    def test_split_file_not_found(self):
        """Valida que o splitter lança FileNotFoundError se o caminho for inexistente."""
        splitter = PDFSplitter(1024)
        with self.assertRaises(FileNotFoundError):
            splitter.split('caminho/inexistente.pdf', self.test_dir)

    def test_split_invalid_pdf(self):
        """Valida que o splitter lança ValueError se o arquivo estiver corrompido ou vazio."""
        corrupted_path = os.path.join(self.test_dir, 'corrupted.pdf')
        with open(corrupted_path, 'wb') as f:
            f.write(b'arquivo de texto qualquer que nao e pdf')

        splitter = PDFSplitter(1024)
        with self.assertRaises(ValueError):
            splitter.split(corrupted_path, self.test_dir)

    @patch('subprocess.run')
    def test_compress_happy_path(self, mock_run):
        """Testa se a compressão Ghostscript simula corretamente chamada com sucesso."""
        mock_run.return_value = MagicMock(returncode=0)
        output_path = os.path.join(self.test_dir, 'compressed.pdf')
        
        # Como o Ghostscript real é mockado, gravamos bytes de PDF fake
        def fake_gs_effect(*args, **kwargs):
            with open(output_path, 'wb') as f:
                f.write(b'%PDF-fake-compressed-bytes')
            return mock_run.return_value

        mock_run.side_effect = fake_gs_effect

        compressor = PDFCompressor('medium')
        success = compressor.compress(self.input_path, output_path)
        
        self.assertTrue(success)
        self.assertTrue(os.path.exists(output_path))
        mock_run.assert_called_once()
        self.assertIn('-dPDFSETTINGS=/ebook', mock_run.call_args[0][0])

    @patch('subprocess.run')
    def test_compress_failed_command(self, mock_run):
        """Valida que lança RuntimeError se o Ghostscript retornar erro de execução."""
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=['gs'],
            stderr='Error in ghostscript processing'
        )
        
        output_path = os.path.join(self.test_dir, 'failed.pdf')
        compressor = PDFCompressor('high')
        
        with self.assertRaises(RuntimeError):
            compressor.compress(self.input_path, output_path)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT, CELERY_TASK_ALWAYS_EAGER=False)
class PDFViewsTestCase(TestCase):
    """Testa a integração de Views, Validações e Controle de Sessão."""

    def setUp(self):
        self.pdf_bytes = create_dummy_pdf(num_pages=2)
        session = self.client.session
        session.save()

    def tearDown(self):
        if os.path.exists(TEMP_MEDIA_ROOT):
            shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def test_index_view(self):
        """Testa se a página inicial carrega com sucesso."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Divisor')

    def test_safe_pdf_filename_sanitizes_and_avoids_duplicates(self):
        """Valida normalizacao de nomes antes de gravar uploads em disco."""
        used_names = set()

        first = _safe_pdf_filename('../../relatorio final.pdf', used_names)
        second = _safe_pdf_filename('../../relatorio final.pdf', used_names)
        third = _safe_pdf_filename('', used_names)

        self.assertEqual(first, 'relatorio_final.pdf')
        self.assertEqual(second, 'relatorio_final_2.pdf')
        self.assertEqual(third, 'arquivo.pdf')

    @patch('splitter.tasks.process_split_job.delay')
    def test_upload_split_only_happy_path(self, mock_delay):
        """Testa o upload de arquivos válidos solicitando apenas a divisão."""
        mock_delay.return_value = MagicMock(id='fake_task_id_123')
        
        pdf_file = SimpleUploadedFile(
            name='test1.pdf',
            content=self.pdf_bytes,
            content_type='application/pdf'
        )
        
        payload = {
            'files': [pdf_file],
            'compress_level': 'none',
            'should_split': 'true',
            'max_size_mb': '2.0'
        }
        
        response = self.client.post('/api/upload/', payload)
        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertIn('job_id', data)
        mock_delay.assert_called_once()

    @patch('splitter.tasks.process_split_job.delay')
    def test_upload_compress_only_happy_path(self, mock_delay):
        """Testa upload de arquivos solicitando apenas compressão (max_size_mb opcional)."""
        mock_delay.return_value = MagicMock(id='fake_task_id_456')
        
        pdf_file = SimpleUploadedFile(
            name='test_compress.pdf',
            content=self.pdf_bytes,
            content_type='application/pdf'
        )
        
        payload = {
            'files': [pdf_file],
            'compress_level': 'medium',
            'should_split': 'false'
        }
        
        response = self.client.post('/api/upload/', payload)
        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertIn('job_id', data)
        mock_delay.assert_called_once()

    def test_upload_no_actions_error(self):
        """Testa erro ao tentar enviar sem solicitar compressão nem divisão."""
        pdf_file = SimpleUploadedFile(name='test.pdf', content=self.pdf_bytes)
        payload = {
            'files': [pdf_file],
            'compress_level': 'none',
            'should_split': 'false'
        }
        response = self.client.post('/api/upload/', payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    def test_upload_invalid_extension_edge_case(self):
        """Caso de Borda: Envio de arquivo com extensão inválida (.txt)."""
        txt_file = SimpleUploadedFile(
            name='fake.txt',
            content=b'Algum texto normal',
            content_type='text/plain'
        )
        payload = {
            'files': [txt_file],
            'compress_level': 'none',
            'should_split': 'true',
            'max_size_mb': '1.0'
        }
        response = self.client.post('/api/upload/', payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('não é um PDF', response.json()['error'])

    def test_upload_invalid_magic_bytes_edge_case(self):
        """Caso de Borda: Arquivo com extensão .pdf mas que não possui magic bytes válidos (%PDF-)."""
        fake_pdf = SimpleUploadedFile(
            name='fake.pdf',
            content=b'Isto nao comeca com por cento PDF',
            content_type='application/pdf'
        )
        payload = {
            'files': [fake_pdf],
            'compress_level': 'none',
            'should_split': 'true',
            'max_size_mb': '1.0'
        }
        response = self.client.post('/api/upload/', payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('não é um PDF válido', response.json()['error'])

    def test_status_invalid_session_edge_case(self):
        """Caso de Borda: Tentativa de acessar status de um job de outra sessão (deve retornar 404)."""
        job = SplitJob.objects.create(
            session_key='outra_session_id_diferente',
            original_filenames=['doc.pdf'],
            compress_level='none',
            should_split=True,
            max_size_mb=10.0
        )
        
        response = self.client.get(f'/api/status/{job.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_download_expired_files_edge_case(self):
        """Caso de Borda: Tentativa de baixar ZIP de job concluído mas já limpo do disco (404)."""
        session_key = self.client.session.session_key
        if not session_key:
            self.client.session.create()
            session_key = self.client.session.session_key

        job = SplitJob.objects.create(
            session_key=session_key,
            original_filenames=['doc.pdf'],
            compress_level='none',
            should_split=True,
            max_size_mb=10.0,
            status=SplitJob.Status.COMPLETED,
            output_zip_path=os.path.join(TEMP_MEDIA_ROOT, 'sessions', session_key, 'output', 'resultado.zip')
        )
        
        response = self.client.get(f'/api/download/{job.pk}/')
        self.assertEqual(response.status_code, 404)
        self.assertIn('removido', response.content.decode('utf-8'))


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class PDFCeleryTasksTestCase(TestCase):
    """Testa a lógica de execução das tasks Celery de forma síncrona."""

    def setUp(self):
        self.pdf_bytes = create_dummy_pdf(num_pages=4)
        
        self.session_key = 'test_celery_session_key'
        self.job = SplitJob.objects.create(
            session_key=self.session_key,
            original_filenames=['test.pdf'],
            compress_level=SplitJob.CompressLevel.NONE,
            should_split=True,
            max_size_mb=0.0001,  # Limite extremamente baixo (~100 bytes) para forçar o split individual de cada página
        )
        
        input_dir = self.job.input_dir
        input_dir.mkdir(parents=True, exist_ok=True)
        self.input_pdf_path = input_dir / 'test.pdf'
        self.input_pdf_path.write_bytes(self.pdf_bytes)

    def tearDown(self):
        if os.path.exists(TEMP_MEDIA_ROOT):
            shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def test_task_process_split_job_happy_path(self):
        """Valida que a execução síncrona da task processa, divide, cria o ZIP e limpa input."""
        result = process_split_job.apply(args=[self.job.pk])
        self.assertTrue(result.successful())

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, SplitJob.Status.COMPLETED)
        self.assertEqual(self.job.progress, 100)
        self.assertEqual(self.job.total_output_files, 4)  # 4 páginas -> 4 PDFs divididos
        self.assertIsNotNone(self.job.total_output_size_mb)
        self.assertTrue(self.job.total_output_size_mb >= 0)
        
        zip_path = os.path.join(TEMP_MEDIA_ROOT, 'sessions', self.session_key, 'output', str(self.job.pk), 'resultado.zip')
        self.assertTrue(os.path.exists(zip_path))
        self.assertFalse(self.job.input_dir.exists())

    def test_task_cleanup_expired_sessions(self):
        """Valida que a task periódica remove arquivos com mais de 1 hora de vida do disco."""
        job_to_clean = SplitJob.objects.create(
            session_key='session_to_clean',
            original_filenames=['old.pdf'],
            compress_level=SplitJob.CompressLevel.NONE,
            should_split=True,
            max_size_mb=10.0,
            status=SplitJob.Status.COMPLETED,
            cleaned_up=False
        )
        
        output_dir = job_to_clean.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        fake_zip = output_dir / 'resultado.zip'
        fake_zip.write_bytes(b'fake zip bytes')
        
        two_hours_ago = timezone.now() - timezone.timedelta(hours=2)
        SplitJob.objects.filter(pk=job_to_clean.pk).update(created_at=two_hours_ago)

        cleanup_expired_sessions.apply()
        
        job_to_clean.refresh_from_db()
        self.assertTrue(job_to_clean.cleaned_up)
        
        self.assertFalse(fake_zip.exists())
        self.assertFalse(output_dir.exists())

    @patch('subprocess.run')
    def test_task_process_split_single_pdf_output_no_zip(self, mock_run):
        """Valida que se o processamento gerar apenas 1 PDF de saída, não cria o ZIP e salva o PDF direto."""
        mock_run.return_value = MagicMock(returncode=0)

        # Configura o job para não dividir (apenas compressão) para garantir 1 único arquivo resultante
        single_job = SplitJob.objects.create(
            session_key='test_single_file_session',
            original_filenames=['test_single.pdf'],
            compress_level=SplitJob.CompressLevel.MEDIUM,
            should_split=False
        )

        input_dir = single_job.input_dir
        input_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = input_dir / 'test_single.pdf'
        pdf_path.write_bytes(self.pdf_bytes)

        # Mock Ghostscript output
        def fake_gs_effect(*args, **kwargs):
            compressed_dir = input_dir / 'compressed'
            compressed_dir.mkdir(parents=True, exist_ok=True)
            compressed_path = compressed_dir / 'compressed_test_single.pdf'
            compressed_path.write_bytes(b'%PDF-fake-compressed')
            return mock_run.return_value

        mock_run.side_effect = fake_gs_effect

        result = process_split_job.apply(args=[single_job.pk])
        self.assertTrue(result.successful())

        single_job.refresh_from_db()
        self.assertEqual(single_job.status, SplitJob.Status.COMPLETED)
        self.assertEqual(single_job.total_output_files, 1)

        # O caminho final de saída deve ser um PDF
        output_path = Path(single_job.output_zip_path)
        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.suffix, '.pdf')
        self.assertIn('test_single_comprimido.pdf', output_path.name)

        # Não deve existir um arquivo resultado.zip nesse diretório
        zip_path = output_path.parent / 'resultado.zip'
        self.assertFalse(zip_path.exists())

    @patch('subprocess.run')
    def test_task_process_split_resulting_in_one_part_nomenclature(self, mock_run):
        """Valida a nomenclatura correta quando o split resulta em apenas 1 arquivo."""
        mock_run.return_value = MagicMock(returncode=0)

        # Configura o job para dividir, mas com limite alto para resultar em apenas 1 parte
        single_part_job = SplitJob.objects.create(
            session_key='test_single_part_session',
            original_filenames=['doc_original.pdf'],
            compress_level=SplitJob.CompressLevel.MEDIUM,
            should_split=True,
            max_size_mb=100.0  # Limite alto
        )

        input_dir = single_part_job.input_dir
        input_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = input_dir / 'doc_original.pdf'
        pdf_path.write_bytes(self.pdf_bytes)

        # Mock Ghostscript output para a compressão inicial e para a compressão intermediária no splitter
        def fake_gs_effect(*args, **kwargs):
            # Encontra se é o de input ou se é o intermediário
            out_arg = [arg for arg in args[0] if arg.startswith('-sOutputFile=')][0]
            out_file = Path(out_arg.split('=')[1])
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(self.pdf_bytes)  # Grava bytes válidos
            return mock_run.return_value

        mock_run.side_effect = fake_gs_effect

        result = process_split_job.apply(args=[single_part_job.pk])
        self.assertTrue(result.successful())

        single_part_job.refresh_from_db()
        self.assertEqual(single_part_job.total_output_files, 1)

        output_path = Path(single_part_job.output_zip_path)
        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.name, 'doc_original_comprimido_dividido.pdf')

    @patch('subprocess.run')
    def test_download_single_pdf_view(self, mock_run):
        """Valida que o download de um job com 1 arquivo único serve um PDF e não um ZIP."""
        mock_run.return_value = MagicMock(returncode=0)
        
        session = self.client.session
        session.save()
        session_key = session.session_key

        # Cria um PDF fake de saída
        out_dir = Path(TEMP_MEDIA_ROOT) / 'sessions' / session_key / 'output' / '999'
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_file = out_dir / 'doc_comprimido.pdf'
        pdf_file.write_bytes(b'%PDF-fake-out')

        job = SplitJob.objects.create(
            session_key=session_key,
            original_filenames=['doc.pdf'],
            compress_level=SplitJob.CompressLevel.MEDIUM,
            should_split=False,
            status=SplitJob.Status.COMPLETED,
            output_zip_path=str(pdf_file),
            total_output_files=1
        )

        response = self.client.get(f'/api/download/{job.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(
            response['Content-Disposition'],
            'attachment; filename="doc_comprimido.pdf"'
        )
