"""Testes do app OCR.

O binário do OCRmyPDF nunca é chamado de verdade aqui: a suíte roda no CI, que
não tem Tesseract instalado, e um teste que depende de OCR real mede a máquina,
não o código. O que se testa é o contrato com ele — a linha de comando montada,
o tratamento de cada código de saída e o que o resto do sistema faz com o
resultado.
"""

import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader, PdfWriter

from legal.models import DocumentoLegal, TipoDocumento

from .models import OCRJob
from .services import (
    OCRProcessor,
    _imagens_da_pagina,
    _reamostrar_com_ghostscript,
    extrair_paginas,
    ocr_disponivel,
    texto_esparso,
    texto_para_markdown,
)
from .tasks import cleanup_expired_ocr_jobs, process_ocr_job

TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix="divisor_pdf_ocr_test_media_")


def pdf_valido(paginas=2):
    """Bytes de um PDF válido e vazio, do tamanho pedido."""
    writer = PdfWriter()
    for _ in range(paginas):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def pdf_com_imagem(paginas=1):
    """Bytes de um PDF em que cada página referencia um objeto de imagem.

    É a forma do arquivo híbrido — slide exportado do PowerPoint, com o corpo
    em imagem — reduzida ao que o diagnóstico olha: imagem presente e texto
    ausente.
    """
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter()
    for _ in range(paginas):
        pagina = writer.add_blank_page(width=612, height=792)
        imagem = DecodedStreamObject()
        imagem.set_data(b"\x00\x00\x00")
        for chave, valor in (
            ("/Type", NameObject("/XObject")),
            ("/Subtype", NameObject("/Image")),
            ("/Width", NumberObject(1)),
            ("/Height", NumberObject(1)),
            ("/ColorSpace", NameObject("/DeviceRGB")),
            ("/BitsPerComponent", NumberObject(8)),
        ):
            imagem[NameObject(chave)] = valor
        referencia = writer._add_object(imagem)
        pagina["/Resources"][NameObject("/XObject")] = DictionaryObject(
            {NameObject("/Im1"): referencia}
        )

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def upload_pdf(nome="documento.pdf", paginas=1):
    return SimpleUploadedFile(nome, pdf_valido(paginas), content_type="application/pdf")


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class ServicosOCRTestCase(TestCase):
    """A linha de comando do OCRmyPDF e a conversão do texto em Markdown."""

    def setUp(self):
        self.dir = Path(TEMP_MEDIA_ROOT) / "servicos"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.entrada = self.dir / "entrada.pdf"
        self.entrada.write_bytes(pdf_valido(2))
        self.saida = self.dir / "saida.pdf"

    def tearDown(self):
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def test_comando_padrao_preserva_texto_existente(self):
        cmd = OCRProcessor(idioma="por").montar_comando(self.entrada, self.saida)

        self.assertIn("--skip-text", cmd)
        self.assertNotIn("--force-ocr", cmd)
        self.assertEqual(cmd[cmd.index("--language") + 1], "por")
        self.assertEqual(cmd[-2:], [str(self.entrada), str(self.saida)])

    def test_comando_com_forcar_refaz_o_ocr(self):
        cmd = OCRProcessor(idioma="por+eng", forcar=True).montar_comando(self.entrada, self.saida)

        self.assertIn("--force-ocr", cmd)
        self.assertNotIn("--skip-text", cmd)
        self.assertEqual(cmd[cmd.index("--language") + 1], "por+eng")

    def test_run_gera_o_pdf_de_saida(self):
        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(pdf_valido(2))
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch("ocr.services.subprocess.run", side_effect=fake_run),
        ):
            self.assertTrue(OCRProcessor().run(self.entrada, self.saida))

        self.assertTrue(self.saida.exists())

    def test_run_falha_quando_a_entrada_nao_existe(self):
        with self.assertRaises(FileNotFoundError):
            OCRProcessor().run(self.dir / "nao-existe.pdf", self.saida)

    def test_run_falha_quando_o_ocrmypdf_nao_esta_instalado(self):
        with (
            patch("ocr.services.shutil.which", return_value=None),
            self.assertRaises(RuntimeError) as ctx,
        ):
            OCRProcessor().run(self.entrada, self.saida)

        self.assertIn("não está disponível", str(ctx.exception))

    def test_run_traduz_o_codigo_de_saida_em_mensagem_util(self):
        # Código 5 é "não deu para ler ou gravar o arquivo": não há segunda
        # tentativa que resolva, então a mensagem vai direto para o usuário.
        erro = subprocess.CalledProcessError(5, "ocrmypdf", stderr="InputFileError")

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch("ocr.services.subprocess.run", side_effect=erro),
            self.assertRaises(RuntimeError) as ctx,
        ):
            OCRProcessor().run(self.entrada, self.saida)

        self.assertIn("ler ou gravar", str(ctx.exception))

    def test_run_refaz_forcando_quando_o_pdf_e_recusado_por_ja_ter_texto(self):
        # Código 6 cobre o PriorOcrFoundError e o TaggedPDFError — este último é
        # o que um PDF exportado do Word ou do PowerPoint devolve, antes de
        # reconhecer coisa alguma. Rasterizar é a única saída.
        chamadas = []

        def fake_run(cmd, **kwargs):
            chamadas.append(cmd)
            if len(chamadas) == 1:
                raise subprocess.CalledProcessError(6, "ocrmypdf", stderr="TaggedPDFError")
            Path(cmd[-1]).write_bytes(pdf_valido(2))
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch("ocr.services.subprocess.run", side_effect=fake_run),
            patch("ocr.services._reamostrar_com_ghostscript") as reamostrar,
        ):
            self.assertTrue(OCRProcessor().run(self.entrada, self.saida))

        self.assertEqual(len(chamadas), 2)
        self.assertIn("--skip-text", chamadas[0])
        self.assertIn("--force-ocr", chamadas[1])
        self.assertTrue(self.saida.exists())
        reamostrar.assert_called_once_with(self.saida)

    def test_run_refaz_forcando_quando_a_saida_sai_hibrida(self):
        # Cada página tem um resto de texto nativo e o corpo em imagem: o
        # --skip-text pulou a página inteira por causa do pouco texto.
        chamadas = []

        def fake_run(cmd, **kwargs):
            chamadas.append(cmd)
            Path(cmd[-1]).write_bytes(pdf_com_imagem(2) if len(chamadas) == 1 else pdf_valido(3))
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch("ocr.services.subprocess.run", side_effect=fake_run),
            patch("ocr.services._reamostrar_com_ghostscript"),
            patch("ocr.services.extrair_paginas", side_effect=[["curto"], ["texto" * 200]]),
        ):
            self.assertTrue(OCRProcessor().run(self.entrada, self.saida))

        self.assertEqual(len(chamadas), 2)
        self.assertIn("--force-ocr", chamadas[1])
        # A saída forçada substituiu a primeira, e o arquivo temporário sumiu.
        self.assertEqual(len(PdfReader(str(self.saida)).pages), 3)
        self.assertEqual(list(self.dir.glob("*_forcado.pdf")), [])

    def test_run_mantem_o_primeiro_resultado_quando_forcar_nao_melhora(self):
        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(pdf_com_imagem(2))
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch("ocr.services.subprocess.run", side_effect=fake_run),
            patch("ocr.services._reamostrar_com_ghostscript") as reamostrar,
            patch("ocr.services.extrair_paginas", side_effect=[["texto" * 200], ["curto"]]),
        ):
            self.assertTrue(OCRProcessor().run(self.entrada, self.saida))

        # Sem ganho de texto, não se troca um PDF válido por um rasterizado.
        self.assertEqual(len(PdfReader(str(self.saida)).pages), 2)
        reamostrar.assert_not_called()

    def test_run_forcado_pelo_usuario_nao_repete_a_passada(self):
        chamadas = []

        def fake_run(cmd, **kwargs):
            chamadas.append(cmd)
            Path(cmd[-1]).write_bytes(pdf_com_imagem(2))
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch("ocr.services.subprocess.run", side_effect=fake_run),
            patch("ocr.services._reamostrar_com_ghostscript"),
        ):
            self.assertTrue(OCRProcessor(forcar=True).run(self.entrada, self.saida))

        self.assertEqual(len(chamadas), 1)

    def test_texto_esparso_so_acusa_pagina_que_tem_imagem(self):
        com_imagem = self.dir / "hibrido.pdf"
        com_imagem.write_bytes(pdf_com_imagem(3))
        self.assertTrue(texto_esparso(com_imagem))

        # Página em branco não tem texto nem imagem: não é PDF híbrido, é PDF
        # vazio, e rasterizar não traria texto nenhum.
        em_branco = self.dir / "branco.pdf"
        em_branco.write_bytes(pdf_valido(3))
        self.assertFalse(texto_esparso(em_branco))

    def test_run_avisa_quando_estoura_o_tempo_limite(self):
        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch(
                "ocr.services.subprocess.run",
                side_effect=subprocess.TimeoutExpired("ocrmypdf", 1800),
            ),
            self.assertRaises(RuntimeError) as ctx,
        ):
            OCRProcessor().run(self.entrada, self.saida)

        self.assertIn("tempo limite", str(ctx.exception))

    def test_run_falha_quando_a_saida_sai_vazia(self):
        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch(
                "ocr.services.subprocess.run",
                return_value=subprocess.CompletedProcess(["ocrmypdf"], 0),
            ),
            self.assertRaises(RuntimeError) as ctx,
        ):
            OCRProcessor().run(self.entrada, self.saida)

        self.assertIn("não gerou o PDF", str(ctx.exception))

    def test_run_desiste_quando_a_passada_forcada_tambem_e_recusada(self):
        erro = subprocess.CalledProcessError(6, "ocrmypdf", stderr="PriorOcrFoundError")

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch("ocr.services.subprocess.run", side_effect=erro) as run,
            self.assertRaises(RuntimeError) as ctx,
        ):
            OCRProcessor().run(self.entrada, self.saida)

        self.assertEqual(run.call_count, 2)
        self.assertIn("refazer OCR", str(ctx.exception))

    def test_run_mantem_o_primeiro_resultado_se_a_passada_forcada_falha(self):
        chamadas = []

        def fake_run(cmd, **kwargs):
            chamadas.append(cmd)
            if len(chamadas) == 1:
                Path(cmd[-1]).write_bytes(pdf_com_imagem(2))
                return subprocess.CompletedProcess(cmd, 0)
            raise subprocess.CalledProcessError(9, "ocrmypdf", stderr="EncryptedPdfError")

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch("ocr.services.subprocess.run", side_effect=fake_run),
            patch("ocr.services._reamostrar_com_ghostscript") as reamostrar,
        ):
            self.assertTrue(OCRProcessor().run(self.entrada, self.saida))

        self.assertEqual(len(chamadas), 2)
        self.assertEqual(len(PdfReader(str(self.saida)).pages), 2)
        reamostrar.assert_not_called()

    def test_run_mantem_o_primeiro_resultado_se_a_passada_forcada_sai_vazia(self):
        chamadas = []

        def fake_run(cmd, **kwargs):
            chamadas.append(cmd)
            if len(chamadas) == 1:
                Path(cmd[-1]).write_bytes(pdf_com_imagem(2))
            else:
                Path(cmd[-1]).write_bytes(b"")
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch("ocr.services.subprocess.run", side_effect=fake_run),
            patch("ocr.services._reamostrar_com_ghostscript"),
        ):
            self.assertTrue(OCRProcessor().run(self.entrada, self.saida))

        self.assertEqual(len(PdfReader(str(self.saida)).pages), 2)
        self.assertEqual(list(self.dir.glob("*_forcado.pdf")), [])

    def test_ocr_disponivel_reflete_o_path(self):
        with patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"):
            self.assertTrue(ocr_disponivel())
        with patch("ocr.services.shutil.which", return_value=None):
            self.assertFalse(ocr_disponivel())

    def test_imagens_da_pagina_tolera_estrutura_fora_do_padrao(self):
        # Página sem /Resources e página cujo dicionário explode ao ser lido:
        # nos dois casos o diagnóstico segue, apenas sem contar imagem.
        self.assertEqual(_imagens_da_pagina({}), 0)

        class PaginaQuebrada:
            def get(self, _chave):
                raise ValueError("dicionário inválido")

        self.assertEqual(_imagens_da_pagina(PaginaQuebrada()), 0)

    def test_run_avisa_quando_nao_consegue_invocar_o_binario(self):
        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf"),
            patch("ocr.services.subprocess.run", side_effect=OSError("Permissão negada")),
            self.assertRaises(RuntimeError) as ctx,
        ):
            OCRProcessor().run(self.entrada, self.saida)

        self.assertIn("Erro ao invocar o OCR", str(ctx.exception))

    def test_texto_esparso_e_falso_em_pdf_sem_pagina_nenhuma(self):
        vazio = self.dir / "sem_paginas.pdf"
        vazio.write_bytes(pdf_valido(0))

        self.assertFalse(texto_esparso(vazio))

    def test_texto_esparso_e_falso_quando_o_pdf_nao_abre(self):
        quebrado = self.dir / "quebrado.pdf"
        quebrado.write_bytes(b"nao sou um pdf")

        # Sem conseguir ler, não há diagnóstico: refazer o OCR às cegas só
        # gastaria o dobro do tempo pelo mesmo resultado.
        self.assertFalse(texto_esparso(quebrado))

    def test_texto_esparso_conta_pagina_que_nao_deixa_extrair_texto(self):
        class PaginaIlegivel:
            def extract_text(self):
                raise ValueError("página ilegível")

            def get(self, chave):
                return None

        class LeitorFalso:
            def __init__(self):
                self.pages = [PaginaIlegivel()]

        with (
            patch("pypdf.PdfReader", return_value=LeitorFalso()),
            patch("ocr.services._imagens_da_pagina", return_value=1),
        ):
            self.assertTrue(texto_esparso(self.entrada))

    def test_extrair_paginas_devolve_uma_entrada_por_pagina(self):
        paginas = extrair_paginas(self.entrada)

        self.assertEqual(len(paginas), 2)

    def test_markdown_marca_titulo_e_paginas(self):
        md = texto_para_markdown(["Primeira linha", "Segunda página"], "contrato")

        self.assertTrue(md.startswith("# contrato\n"))
        self.assertIn("## Página 1", md)
        self.assertIn("## Página 2", md)
        self.assertIn("Segunda página", md)

    def test_markdown_sinaliza_pagina_sem_texto(self):
        md = texto_para_markdown(["   ", ""], "digitalizado")

        self.assertEqual(md.count("*(Nenhum texto reconhecido nesta página.)*"), 2)

    def test_markdown_converte_marcadores_de_lista(self):
        md = texto_para_markdown(["• primeiro\n▪ segundo"], "lista")

        self.assertIn("- primeiro", md)
        self.assertIn("- segundo", md)

    def test_markdown_colapsa_linhas_em_branco_repetidas(self):
        md = texto_para_markdown(["um\n\n\n\n\ndois"], "espacos")

        self.assertNotIn("\n\n\n", md)
        self.assertIn("um", md)
        self.assertIn("dois", md)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class ModeloOCRJobTestCase(TestCase):
    def tearDown(self):
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def test_str_resume_os_arquivos(self):
        job = OCRJob.objects.create(
            session_key="abc", original_filenames=["a.pdf", "b.pdf", "c.pdf", "d.pdf"]
        )

        self.assertIn("(+1)", str(job))
        self.assertIn("pending", str(job))

    def test_diretorios_nao_colidem_com_os_do_divisor(self):
        job = OCRJob.objects.create(session_key="abc", original_filenames=["a.pdf"])

        self.assertIn("ocr_input", str(job.input_dir))
        self.assertIn("ocr_output", str(job.output_dir))

    def test_caminho_do_formato(self):
        job = OCRJob.objects.create(
            session_key="abc", output_pdf_path="/tmp/a.pdf", output_md_path="/tmp/a.md"
        )

        self.assertEqual(job.caminho_do_formato("pdf"), "/tmp/a.pdf")
        self.assertEqual(job.caminho_do_formato("md"), "/tmp/a.md")


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class TaskOCRTestCase(TestCase):
    """O fluxo completo da task, com o OCRmyPDF trocado por um dublê."""

    def tearDown(self):
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def _criar_job(self, nomes=("documento.pdf",)):
        job = OCRJob.objects.create(
            session_key="sessao-teste",
            original_filenames=list(nomes),
            total_input_size_mb=0.1,
        )
        job.input_dir.mkdir(parents=True, exist_ok=True)
        for nome in nomes:
            (job.input_dir / nome).write_bytes(pdf_valido(2))
        return job

    @staticmethod
    def _ocr_falso(_self, input_path, output_path):
        Path(output_path).write_bytes(pdf_valido(2))
        return True

    def test_job_com_um_arquivo_entrega_pdf_e_md_direto(self):
        job = self._criar_job()

        with (
            patch.object(OCRProcessor, "run", self._ocr_falso),
            patch("ocr.services.extrair_paginas", return_value=["Texto reconhecido", ""]),
        ):
            process_ocr_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, OCRJob.Status.COMPLETED)
        self.assertEqual(job.progress, 100)
        self.assertEqual(job.total_output_files, 1)
        self.assertTrue(job.output_pdf_path.endswith("_ocr.pdf"))
        self.assertTrue(job.output_md_path.endswith("_ocr.md"))
        self.assertIn("Texto reconhecido", Path(job.output_md_path).read_text(encoding="utf-8"))
        # Os originais saem do disco assim que o resultado está pronto.
        self.assertFalse(job.input_dir.exists())

    def test_job_com_varios_arquivos_entrega_um_zip_por_formato(self):
        job = self._criar_job(nomes=("a.pdf", "b.pdf"))

        with (
            patch.object(OCRProcessor, "run", self._ocr_falso),
            patch("ocr.services.extrair_paginas", return_value=["texto"]),
        ):
            process_ocr_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, OCRJob.Status.COMPLETED)
        self.assertEqual(job.total_output_files, 2)
        self.assertTrue(job.output_pdf_path.endswith("resultado_ocr_pdf.zip"))
        self.assertTrue(job.output_md_path.endswith("resultado_ocr_md.zip"))
        self.assertTrue(Path(job.output_pdf_path).exists())
        self.assertTrue(Path(job.output_md_path).exists())

    def test_arquivo_problematico_vira_aviso_sem_derrubar_os_outros(self):
        job = self._criar_job(nomes=("a.pdf", "b.pdf"))

        def run_alternado(_self, input_path, output_path):
            if Path(input_path).name == "a.pdf":
                raise RuntimeError("O PDF está protegido por senha.")
            Path(output_path).write_bytes(pdf_valido(1))
            return True

        with (
            patch.object(OCRProcessor, "run", run_alternado),
            patch("ocr.services.extrair_paginas", return_value=["texto"]),
        ):
            process_ocr_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, OCRJob.Status.COMPLETED)
        self.assertEqual(job.total_output_files, 1)
        self.assertTrue(any("a.pdf" in aviso for aviso in job.processing_warnings))

    def test_pagina_sem_texto_gera_aviso(self):
        job = self._criar_job()

        with (
            patch.object(OCRProcessor, "run", self._ocr_falso),
            patch("ocr.services.extrair_paginas", return_value=["", "  "]),
        ):
            process_ocr_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.total_caracteres, 0)
        self.assertTrue(any("Nenhum texto" in aviso for aviso in job.processing_warnings))

    def test_falha_em_todos_os_arquivos_marca_o_job_como_falho(self):
        job = self._criar_job()

        def run_sempre_falha(_self, input_path, output_path):
            raise RuntimeError("Falha no OCR (código 15).")

        with patch.object(OCRProcessor, "run", run_sempre_falha):
            process_ocr_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, OCRJob.Status.FAILED)
        self.assertIn("Nenhum arquivo pôde ser reconhecido", job.error_message)

    def test_job_sem_arquivos_falha_com_mensagem_clara(self):
        job = OCRJob.objects.create(session_key="sessao-teste", original_filenames=[])
        job.input_dir.mkdir(parents=True, exist_ok=True)

        process_ocr_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, OCRJob.Status.FAILED)

    def test_job_inexistente_nao_explode(self):
        # A task pode chegar depois da limpeza; ela apenas registra e sai.
        self.assertIsNone(process_ocr_job(999999))

    def test_limpeza_remove_os_diretorios_expirados(self):
        job = self._criar_job()
        job.output_dir.mkdir(parents=True, exist_ok=True)
        (job.output_dir / "saida.pdf").write_bytes(b"%PDF-")
        job.status = OCRJob.Status.COMPLETED
        job.save(update_fields=["status"])
        OCRJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timezone.timedelta(seconds=7200)
        )

        cleanup_expired_ocr_jobs()

        job.refresh_from_db()
        self.assertTrue(job.cleaned_up)
        self.assertFalse(job.output_dir.exists())
        self.assertFalse(job.input_dir.exists())

    def test_limpeza_preserva_job_recente(self):
        job = self._criar_job()
        job.status = OCRJob.Status.COMPLETED
        job.save(update_fields=["status"])

        cleanup_expired_ocr_jobs()

        job.refresh_from_db()
        self.assertFalse(job.cleaned_up)
        self.assertTrue(job.input_dir.exists())


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class ViewsOCRTestCase(TestCase):
    """Upload, status e download, com o OCR sempre disponível salvo onde dito."""

    def setUp(self):
        self.upload_url = reverse("ocr:upload")
        self.patcher = patch("ocr.services.shutil.which", return_value="/usr/bin/ocrmypdf")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        # A task real chamaria o OCRmyPDF; aqui basta saber que foi enfileirada.
        self.task_patcher = patch("ocr.tasks.process_ocr_job")
        self.task_mock = self.task_patcher.start()
        self.addCleanup(self.task_patcher.stop)

    def tearDown(self):
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def test_pagina_do_ocr_responde(self):
        response = self.client.get(reverse("ocr:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ocr/index.html")
        self.assertContains(response, "Idioma do documento")

    def test_pagina_do_divisor_leva_ao_ocr(self):
        response = self.client.get("/")

        self.assertContains(response, reverse("ocr:index"))

    def test_upload_cria_o_job_e_devolve_202(self):
        response = self.client.post(
            self.upload_url, {"files": [upload_pdf()], "idioma": "por+eng", "forcar_ocr": "true"}
        )

        self.assertEqual(response.status_code, 202)
        job = OCRJob.objects.get(pk=response.json()["job_id"])
        self.assertEqual(job.idioma, "por+eng")
        self.assertTrue(job.forcar_ocr)
        self.assertEqual(job.original_filenames, ["documento.pdf"])
        self.assertTrue((job.input_dir / "documento.pdf").exists())

    def test_upload_sem_arquivos(self):
        response = self.client.post(self.upload_url, {"idioma": "por"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("pelo menos um arquivo", response.json()["error"])

    def test_upload_recusa_extensao_diferente_de_pdf(self):
        arquivo = SimpleUploadedFile("nota.txt", b"%PDF-nao", content_type="text/plain")

        response = self.client.post(self.upload_url, {"files": [arquivo], "idioma": "por"})

        self.assertEqual(response.status_code, 400)

    def test_upload_recusa_arquivo_que_so_tem_nome_de_pdf(self):
        arquivo = SimpleUploadedFile("falso.pdf", b"nao sou um pdf", content_type="application/pdf")

        response = self.client.post(self.upload_url, {"files": [arquivo], "idioma": "por"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("não é um PDF válido", response.json()["error"])

    def test_upload_recusa_idioma_desconhecido(self):
        response = self.client.post(self.upload_url, {"files": [upload_pdf()], "idioma": "klingon"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Idioma inválido", response.json()["error"])

    @override_settings(MAX_UPLOAD_SIZE=10, MAX_UPLOAD_SIZE_MB=0.00001)
    def test_upload_recusa_arquivo_acima_do_limite(self):
        response = self.client.post(self.upload_url, {"files": [upload_pdf()], "idioma": "por"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("excede", response.json()["error"])

    def test_upload_responde_503_quando_o_ocr_nao_esta_instalado(self):
        with patch("ocr.services.shutil.which", return_value=None):
            response = self.client.post(self.upload_url, {"files": [upload_pdf()], "idioma": "por"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(OCRJob.objects.count(), 0)

    def test_upload_exige_aceite_quando_ha_documento_vigente(self):
        DocumentoLegal.objects.create(
            tipo=TipoDocumento.TERMOS, versao="1.0", titulo="Termos", corpo_md="# Termos"
        ).publicar()

        response = self.client.post(self.upload_url, {"files": [upload_pdf()], "idioma": "por"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Termos de Uso", response.json()["error"])
        self.assertEqual(OCRJob.objects.count(), 0)

    def test_upload_registra_o_aceite_enviado_no_formulario(self):
        DocumentoLegal.objects.create(
            tipo=TipoDocumento.TERMOS, versao="1.0", titulo="Termos", corpo_md="# Termos"
        ).publicar()

        response = self.client.post(
            self.upload_url,
            {"files": [upload_pdf()], "idioma": "por", "aceite_legal": "on"},
        )

        self.assertEqual(response.status_code, 202)

    def _job_concluido(self):
        """Cria um job já concluído, com os dois formatos em disco."""
        self.client.post(self.upload_url, {"files": [upload_pdf()], "idioma": "por"})
        job = OCRJob.objects.get()
        job.output_dir.mkdir(parents=True, exist_ok=True)
        pdf = job.output_dir / "documento_ocr.pdf"
        md = job.output_dir / "documento_ocr.md"
        pdf.write_bytes(pdf_valido(1))
        md.write_text("# documento\n", encoding="utf-8")
        job.status = OCRJob.Status.COMPLETED
        job.progress = 100
        job.total_output_files = 1
        job.total_caracteres = 42
        job.output_pdf_path = str(pdf)
        job.output_md_path = str(md)
        job.output_pdf_size_mb = 0.01
        job.output_md_size_mb = 0.0
        job.save()
        return job

    def test_status_traz_as_urls_dos_dois_formatos(self):
        job = self._job_concluido()

        dados = self.client.get(reverse("ocr:status", args=[job.pk])).json()

        self.assertEqual(dados["status"], "completed")
        self.assertEqual(dados["total_caracteres"], 42)
        self.assertEqual(dados["download_urls"]["pdf"], f"/ocr/api/download/{job.pk}/pdf/")
        self.assertEqual(dados["download_urls"]["md"], f"/ocr/api/download/{job.pk}/md/")

    def test_status_de_job_de_outra_sessao_da_404(self):
        job = self._job_concluido()
        self.client.cookies.clear()

        response = self.client.get(reverse("ocr:status", args=[job.pk]))

        self.assertEqual(response.status_code, 404)

    def test_status_de_job_inexistente_da_404(self):
        self.assertEqual(self.client.get(reverse("ocr:status", args=[999])).status_code, 404)

    def test_status_de_job_falho_traz_a_mensagem(self):
        self.client.post(self.upload_url, {"files": [upload_pdf()], "idioma": "por"})
        job = OCRJob.objects.get()
        job.status = OCRJob.Status.FAILED
        job.error_message = "O PDF está protegido por senha."
        job.save()

        dados = self.client.get(reverse("ocr:status", args=[job.pk])).json()

        self.assertEqual(dados["error_message"], "O PDF está protegido por senha.")

    def test_download_do_pdf(self):
        job = self._job_concluido()

        response = self.client.get(reverse("ocr:download", args=[job.pk, "pdf"]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("documento_ocr.pdf", response["Content-Disposition"])

    def test_download_do_markdown(self):
        job = self._job_concluido()

        response = self.client.get(reverse("ocr:download", args=[job.pk, "md"]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/markdown", response["Content-Type"])

    def test_download_recusa_formato_desconhecido(self):
        job = self._job_concluido()

        response = self.client.get(reverse("ocr:download", args=[job.pk, "docx"]))

        self.assertEqual(response.status_code, 400)

    def test_download_antes_de_concluir(self):
        self.client.post(self.upload_url, {"files": [upload_pdf()], "idioma": "por"})
        job = OCRJob.objects.get()

        response = self.client.get(reverse("ocr:download", args=[job.pk, "pdf"]))

        self.assertEqual(response.status_code, 400)

    def test_download_apos_a_limpeza_explica_o_sumico(self):
        job = self._job_concluido()
        Path(job.output_pdf_path).unlink()

        response = self.client.get(reverse("ocr:download", args=[job.pk, "pdf"]))

        self.assertEqual(response.status_code, 404)
        self.assertIn("removido", response.content.decode())

    def test_download_de_job_de_outra_sessao_da_404(self):
        job = self._job_concluido()
        self.client.cookies.clear()

        response = self.client.get(reverse("ocr:download", args=[job.pk, "pdf"]))

        self.assertEqual(response.status_code, 404)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT, OCR_RASTER_DPI=150)
class ReamostragemTestCase(TestCase):
    """A redução do PDF rasterizado pelo Ghostscript.

    Ela nunca pode piorar o resultado: o PDF forçado já está correto, e o
    Ghostscript aqui é só economia de disco e de banda.
    """

    def setUp(self):
        self.dir = Path(TEMP_MEDIA_ROOT) / "reamostragem"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.pdf = self.dir / "forcado.pdf"
        self.pdf.write_bytes(pdf_valido(4))
        self.original = self.pdf.read_bytes()

    def tearDown(self):
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    @staticmethod
    def _saida_de(cmd):
        prefixo = "-sOutputFile="
        return Path(next(arg for arg in cmd if arg.startswith(prefixo))[len(prefixo) :])

    def test_substitui_o_arquivo_quando_o_resultado_encolhe(self):
        menor = pdf_valido(1)

        def fake_run(cmd, **kwargs):
            self._saida_de(cmd).write_bytes(menor)
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/gs"),
            patch("ocr.services.subprocess.run", side_effect=fake_run) as run,
        ):
            _reamostrar_com_ghostscript(self.pdf)

        self.assertEqual(self.pdf.read_bytes(), menor)
        self.assertIn("-dColorImageResolution=150", run.call_args[0][0])
        self.assertIn("-dMonoImageResolution=300", run.call_args[0][0])

    def test_mantem_o_arquivo_quando_o_resultado_nao_encolhe(self):
        def fake_run(cmd, **kwargs):
            self._saida_de(cmd).write_bytes(pdf_valido(9))
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/gs"),
            patch("ocr.services.subprocess.run", side_effect=fake_run),
        ):
            _reamostrar_com_ghostscript(self.pdf)

        self.assertEqual(self.pdf.read_bytes(), self.original)

    def test_mantem_o_arquivo_quando_a_saida_sai_vazia(self):
        def fake_run(cmd, **kwargs):
            self._saida_de(cmd).write_bytes(b"")
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/gs"),
            patch("ocr.services.subprocess.run", side_effect=fake_run),
        ):
            _reamostrar_com_ghostscript(self.pdf)

        self.assertEqual(self.pdf.read_bytes(), self.original)

    def test_mantem_o_arquivo_quando_o_ghostscript_falha(self):
        with (
            patch("ocr.services.shutil.which", return_value="/usr/bin/gs"),
            patch(
                "ocr.services.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "gs"),
            ),
        ):
            _reamostrar_com_ghostscript(self.pdf)

        self.assertEqual(self.pdf.read_bytes(), self.original)

    def test_nao_faz_nada_sem_ghostscript_instalado(self):
        with (
            patch("ocr.services.shutil.which", return_value=None),
            patch("ocr.services.subprocess.run") as run,
        ):
            _reamostrar_com_ghostscript(self.pdf)

        run.assert_not_called()
        self.assertEqual(self.pdf.read_bytes(), self.original)
