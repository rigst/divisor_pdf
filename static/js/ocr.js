/**
 * DIVISOR PDF — OCR
 * Seleção de arquivos (drag & drop / input), validações locais, envio com
 * progresso real, acompanhamento do job no servidor e download do resultado
 * em PDF pesquisável ou Markdown.
 *
 * Mesma estrutura do app.js do divisor, sem as opções de compressão/divisão.
 */

document.addEventListener('DOMContentLoaded', () => {
    const siteNav = document.getElementById('site-nav');
    if (siteNav) {
        const onScroll = () => siteNav.classList.toggle('is-scrolled', window.scrollY > 20);
        window.addEventListener('scroll', onScroll, { passive: true });
        onScroll();
    }

    // --- DOM ---
    const uploadSection = document.getElementById('upload-section');
    const ocrForm = document.getElementById('ocr-form');
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const fileList = document.getElementById('file-list');
    const fileItems = document.getElementById('file-items');
    const fileCountText = document.getElementById('file-count');
    const fileTotalSizeText = document.getElementById('file-total-size');
    const clearFilesBtn = document.getElementById('clear-files-btn');

    const langControl = document.getElementById('lang-control');
    const langCards = document.querySelectorAll('#lang-control .compress-opt-card');
    const forceControl = document.getElementById('force-control');
    const forcarOcrCheckbox = document.getElementById('forcar-ocr-checkbox');
    const timeEstimate = document.getElementById('time-estimate');
    const timeEstimateTotal = document.getElementById('time-estimate-total');
    const submitBtn = document.getElementById('submit-btn');
    const aceiteWrapper = document.getElementById('aceite-wrapper');
    const aceiteCheckbox = document.getElementById('id_aceite_legal');

    const processingSection = document.getElementById('processing-section');
    const processingTitle = document.getElementById('processing-title');
    const workflowEta = document.getElementById('workflow-eta');
    const uploadProgressContainer = document.getElementById('upload-progress-container');
    const uploadProgressFill = document.getElementById('upload-progress-fill');
    const uploadProgressText = document.getElementById('upload-progress-text');
    const uploadProgressPercent = document.getElementById('upload-progress-percent');
    const processingProgressContainer = document.getElementById('processing-progress-container');
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');
    const progressPercent = document.getElementById('progress-percent');

    const resultSection = document.getElementById('result-section');
    const resultFiles = document.getElementById('result-files');
    const resultChars = document.getElementById('result-chars');
    const downloadPdfBtn = document.getElementById('download-pdf-btn');
    const downloadMdBtn = document.getElementById('download-md-btn');
    const pdfSizeHint = document.getElementById('pdf-size-hint');
    const mdSizeHint = document.getElementById('md-size-hint');
    const newOcrBtn = document.getElementById('new-ocr-btn');
    const resultWarnings = document.getElementById('result-warnings');
    const resultWarningsList = document.getElementById('result-warnings-list');

    const errorSection = document.getElementById('error-section');
    const errorMessage = document.getElementById('error-message');
    const retryBtn = document.getElementById('retry-btn');

    // --- Estado ---
    let selectedFiles = [];
    let pollingInterval = null;
    let etaTickInterval = null;
    let etaDeadline = null;
    let processingStartedAt = null;
    let lastProcessingProgress = 0;

    const MAX_FILE_SIZE_MB = Number(ocrForm.dataset.maxUploadSizeMb || 500);
    const MAX_TOTAL_SIZE_MB = Number(ocrForm.dataset.maxTotalUploadMb || 2048);

    // Chave própria: o job de OCR e o de divisão podem coexistir na mesma sessão.
    const ACTIVE_JOB_KEY = 'divisorpdf:activeOcrJob';

    function saveActiveJob(jobId) {
        try {
            localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify({ jobId, ts: Date.now() }));
        } catch (e) { /* localStorage indisponível: degrada sem quebrar */ }
    }
    function clearActiveJob() {
        try { localStorage.removeItem(ACTIVE_JOB_KEY); } catch (e) {}
    }
    function getActiveJob() {
        try { return JSON.parse(localStorage.getItem(ACTIVE_JOB_KEY) || 'null'); }
        catch (e) { return null; }
    }

    function showToast(message, type = 'error') {
        const dsType = { error: 'danger', success: 'success', warn: 'warn', info: 'info' }[type] || 'danger';
        if (typeof window.dsToast === 'function') {
            window.dsToast(message, dsType);
        } else {
            window.alert(message);
        }
    }

    function formatBytes(bytes, decimals = 2) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + ' ' + sizes[i];
    }

    function formatDuration(seconds) {
        if (!Number.isFinite(seconds) || seconds <= 0) return 'menos de 1s';
        const total = Math.max(1, Math.round(seconds));
        const minutes = Math.floor(total / 60);
        const rest = total % 60;
        if (minutes <= 0) return `${rest}s`;
        return `${minutes}min ${rest.toString().padStart(2, '0')}s`;
    }

    // --- Previsão de tempo ---
    // OCR é dominado pelo Tesseract, e o custo acompanha o volume de imagem, não
    // o número de arquivos: ~9s por MB é o que se mede em digitalização comum
    // de 200-300 DPI. Dois idiomas juntos custam perto de 40% a mais.
    function estimateSeconds() {
        const totalMb = getTotalSelectedBytes() / (1024 * 1024);
        const idioma = getIdioma();
        const fator = idioma === 'por+eng' ? 1.4 : 1;
        const upload = Math.max(1, totalMb / 8);
        const setup = 3 + selectedFiles.length * 1.5;
        const reconhecimento = Math.max(5, totalMb * 9 * fator) * (forcarOcrCheckbox.checked ? 1.3 : 1);
        return { upload, processamento: setup + reconhecimento, total: upload + setup + reconhecimento };
    }

    function getTotalSelectedBytes() {
        return selectedFiles.reduce((acc, f) => acc + f.size, 0);
    }

    function getIdioma() {
        const marcado = document.querySelector('input[name="idioma"]:checked');
        return marcado ? marcado.value : 'por';
    }

    function updateInitialEstimate() {
        if (selectedFiles.length === 0) {
            timeEstimate.style.display = 'none';
            return;
        }
        timeEstimate.style.display = 'block';
        timeEstimateTotal.textContent = `~ ${formatDuration(estimateSeconds().total)}`;
    }

    function setEtaDeadline(seconds) {
        etaDeadline = Date.now() + Math.max(seconds, 1) * 1000;
        renderEta();
    }

    function shortenEtaDeadline(seconds) {
        const candidato = Date.now() + Math.max(seconds, 1) * 1000;
        // Só encurta: uma previsão que aumenta a cada leitura passa a impressão
        // de que o processamento travou.
        if (etaDeadline === null || candidato < etaDeadline) etaDeadline = candidato;
        renderEta();
    }

    function renderEta() {
        if (etaDeadline === null) return;
        const restante = (etaDeadline - Date.now()) / 1000;
        workflowEta.textContent = restante <= 0
            ? 'Tempo restante total: finalizando...'
            : `Tempo restante total: ~ ${formatDuration(restante)}`;
    }

    function startEtaTicker() {
        stopEtaTicker();
        etaTickInterval = setInterval(renderEta, 1000);
    }

    function stopEtaTicker() {
        if (etaTickInterval) {
            clearInterval(etaTickInterval);
            etaTickInterval = null;
        }
    }

    // --- Seleção de arquivos ---
    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            fileInput.click();
        }
    });

    ['dragenter', 'dragover'].forEach(evt => {
        dropZone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.add('drag-over');
        });
    });

    ['dragleave', 'drop'].forEach(evt => {
        dropZone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.remove('drag-over');
        });
    });

    dropZone.addEventListener('drop', (e) => {
        if (e.dataTransfer && e.dataTransfer.files) handleFiles(e.dataTransfer.files);
    });

    fileInput.addEventListener('change', (e) => handleFiles(e.target.files));

    function handleFiles(files) {
        let totalAtual = getTotalSelectedBytes();

        for (const file of files) {
            if (!file.name.toLowerCase().endsWith('.pdf')) {
                showToast(`O arquivo "${file.name}" não é um PDF.`);
                continue;
            }
            const duplicado = selectedFiles.some(f => f.name === file.name && f.size === file.size);
            if (duplicado) continue;

            if (file.size / (1024 * 1024) > MAX_FILE_SIZE_MB) {
                showToast(`O arquivo "${file.name}" excede o limite de ${MAX_FILE_SIZE_MB} MB.`);
                continue;
            }
            if ((totalAtual + file.size) / (1024 * 1024) > MAX_TOTAL_SIZE_MB) {
                showToast(`O tamanho total acumulado excede o limite de ${MAX_TOTAL_SIZE_MB} MB.`);
                break;
            }

            selectedFiles.push(file);
            totalAtual += file.size;
        }

        renderFileList();
    }

    function removeFile(index) {
        selectedFiles.splice(index, 1);
        renderFileList();
    }

    clearFilesBtn.addEventListener('click', () => {
        selectedFiles = [];
        renderFileList();
    });

    function renderFileList() {
        fileItems.innerHTML = '';

        if (selectedFiles.length === 0) {
            fileList.style.display = 'none';
            langControl.style.display = 'none';
            forceControl.style.display = 'none';
            timeEstimate.style.display = 'none';
            submitBtn.style.display = 'none';
            submitBtn.disabled = true;
            fileInput.value = '';
            return;
        }

        selectedFiles.forEach((file, index) => {
            const li = document.createElement('li');
            li.className = 'file-item';

            const icone = document.createElement('div');
            icone.className = 'file-item-icon';
            icone.textContent = 'PDF';

            const info = document.createElement('div');
            info.className = 'file-item-info';
            const nome = document.createElement('div');
            nome.className = 'file-item-name';
            // textContent, e não innerHTML: o nome vem do disco do usuário e
            // pode conter marcação.
            nome.textContent = file.name;
            nome.title = file.name;
            const tamanho = document.createElement('div');
            tamanho.className = 'file-item-size';
            tamanho.textContent = formatBytes(file.size);
            info.append(nome, tamanho);

            const remover = document.createElement('button');
            remover.type = 'button';
            remover.className = 'file-item-remove';
            remover.setAttribute('aria-label', `Remover ${file.name}`);
            remover.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
            remover.addEventListener('click', () => removeFile(index));

            li.append(icone, info, remover);
            fileItems.appendChild(li);
        });

        const totalSize = getTotalSelectedBytes();
        fileCountText.textContent = `${selectedFiles.length} ${selectedFiles.length === 1 ? 'arquivo' : 'arquivos'}`;
        fileTotalSizeText.textContent = formatBytes(totalSize);

        fileList.style.display = 'block';
        langControl.style.display = 'block';
        forceControl.style.display = 'block';
        if (aceiteWrapper) aceiteWrapper.style.display = 'block';
        submitBtn.style.display = 'inline-flex';
        submitBtn.disabled = !aceiteOk();
        updateInitialEstimate();
    }

    // --- Idioma e opções ---
    langCards.forEach(card => {
        card.addEventListener('click', () => {
            langCards.forEach(c => c.classList.remove('active'));
            card.classList.add('active');
            updateInitialEstimate();
        });
    });

    forcarOcrCheckbox.addEventListener('change', updateInitialEstimate);

    // --- Aceite dos termos ---
    // O checkbox só existe até a sessão aceitar; sem ele, nada a validar aqui
    // (o servidor continua sendo quem decide).
    function aceiteOk() {
        return !aceiteCheckbox || aceiteCheckbox.checked;
    }

    if (aceiteCheckbox) {
        aceiteCheckbox.addEventListener('change', () => {
            submitBtn.disabled = selectedFiles.length === 0 || !aceiteOk();
        });
    }

    // --- Envio ---
    ocrForm.addEventListener('submit', (e) => {
        e.preventDefault();

        if (selectedFiles.length === 0) {
            showToast('Selecione pelo menos um arquivo PDF.');
            return;
        }
        if (!aceiteOk()) {
            showToast('É preciso aceitar os Termos de Uso e a Política de Privacidade.');
            return;
        }

        const formData = new FormData();
        selectedFiles.forEach(file => formData.append('files', file));
        formData.append('idioma', getIdioma());
        formData.append('forcar_ocr', forcarOcrCheckbox.checked ? 'true' : 'false');
        if (aceiteCheckbox && aceiteCheckbox.checked) formData.append('aceite_legal', 'on');

        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;

        submitBtn.disabled = true;
        submitBtn.classList.add('loading');
        const textoOriginal = submitBtn.querySelector('span').textContent;
        submitBtn.querySelector('span').textContent = 'Enviando arquivos...';

        processingTitle.textContent = 'Enviando...';
        uploadProgressFill.style.width = '0%';
        uploadProgressPercent.textContent = '0%';
        uploadProgressText.textContent = 'Iniciando upload dos arquivos...';
        uploadProgressContainer.style.display = 'block';
        processingProgressContainer.style.display = 'none';
        stopEtaTicker();
        setEtaDeadline(estimateSeconds().total);
        startEtaTicker();
        showSection(processingSection);

        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/ocr/api/upload/', true);
        xhr.setRequestHeader('X-CSRFToken', csrfToken);
        const uploadStartedAt = Date.now();

        xhr.upload.addEventListener('progress', (evt) => {
            if (!evt.lengthComputable) return;
            const percent = Math.round((evt.loaded / evt.total) * 100);
            uploadProgressFill.style.width = `${percent}%`;
            uploadProgressPercent.textContent = `${percent}%`;
            const loadedMb = (evt.loaded / (1024 * 1024)).toFixed(1);
            const totalMb = (evt.total / (1024 * 1024)).toFixed(1);
            uploadProgressText.textContent = `Enviando arquivos (${loadedMb} MB de ${totalMb} MB)...`;

            if (evt.loaded > 0 && percent < 100) {
                const decorrido = (Date.now() - uploadStartedAt) / 1000;
                const taxa = evt.loaded / Math.max(decorrido, 0.1);
                const restanteUpload = Math.max(evt.total - evt.loaded, 0) / taxa;
                shortenEtaDeadline(restanteUpload + estimateSeconds().processamento);
            }
        });

        xhr.onload = function () {
            if (xhr.status >= 200 && xhr.status < 300) {
                try {
                    const data = JSON.parse(xhr.responseText);
                    uploadProgressFill.style.width = '100%';
                    uploadProgressPercent.textContent = '100%';
                    uploadProgressText.textContent = 'Upload concluído!';

                    processingTitle.textContent = 'Reconhecendo...';
                    processingProgressContainer.style.display = 'block';
                    progressFill.style.width = '0%';
                    progressPercent.textContent = '0%';
                    progressText.textContent = 'Iniciando o OCR no servidor...';

                    saveActiveJob(data.job_id);
                    startPolling(data.job_id);
                } catch (err) {
                    falhaNoEnvio('Resposta inválida do servidor.');
                }
            } else {
                try {
                    const data = JSON.parse(xhr.responseText);
                    falhaNoEnvio(data.error || 'Ocorreu um erro no upload.');
                } catch (err) {
                    falhaNoEnvio('Falha no upload dos arquivos.');
                }
            }
        };

        xhr.onerror = () => falhaNoEnvio('Erro de conexão durante o upload.');

        function falhaNoEnvio(mensagem) {
            stopEtaTicker();
            submitBtn.disabled = false;
            submitBtn.classList.remove('loading');
            submitBtn.querySelector('span').textContent = textoOriginal;
            showSection(uploadSection);
            showToast(mensagem);
        }

        xhr.send(formData);
    });

    // --- Acompanhamento do job ---
    function startPolling(jobId) {
        processingStartedAt = Date.now();
        lastProcessingProgress = 0;
        updateProgress(0, 'Iniciando o reconhecimento...');

        let errosSeguidos = 0;
        const MAX_POLL_ERRORS = 8;

        pollingInterval = setInterval(async () => {
            try {
                const response = await fetch(`/ocr/api/status/${jobId}/`);
                const data = await response.json();

                if (!response.ok) {
                    if (response.status === 404) {
                        stopPolling();
                        stopEtaTicker();
                        clearActiveJob();
                        showFailure(data.error || 'Este processamento não está mais disponível.');
                        return;
                    }
                    throw new Error(data.error || 'Erro ao obter status do processamento.');
                }

                errosSeguidos = 0;

                if (data.status === 'pending') {
                    updateProgress(data.progress || 0, 'Aguardando na fila do servidor...');
                } else if (data.status === 'processing') {
                    updateProgress(data.progress || 0, 'Reconhecendo o texto página a página...');
                } else if (data.status === 'completed') {
                    stopPolling();
                    stopEtaTicker();
                    updateProgress(100, 'Reconhecimento concluído.');
                    workflowEta.textContent = 'Tempo restante total: concluído';
                    clearActiveJob();
                    showSuccess(data);
                } else if (data.status === 'failed') {
                    stopPolling();
                    stopEtaTicker();
                    clearActiveJob();
                    showFailure(data.error_message || 'Erro inesperado no servidor.');
                }
            } catch (error) {
                // Rede instável não é motivo para desistir: o job segue no servidor.
                errosSeguidos++;
                if (errosSeguidos >= MAX_POLL_ERRORS) {
                    stopPolling();
                    stopEtaTicker();
                    showFailure('Conexão perdida com o servidor. O processamento pode continuar — recarregue a página para retomar.');
                } else {
                    progressText.textContent = 'Conexão instável — tentando reconectar...';
                }
            }
        }, 2000);
    }

    function stopPolling() {
        if (pollingInterval) {
            clearInterval(pollingInterval);
            pollingInterval = null;
        }
    }

    function updateProgress(percent, mensagem) {
        const valor = Math.min(Math.max(Number(percent) || 0, 0), 100);
        progressFill.style.width = `${valor}%`;
        progressPercent.textContent = `${valor}%`;
        progressText.textContent = mensagem;

        if (!processingStartedAt || valor <= 0 || valor <= lastProcessingProgress) return;

        lastProcessingProgress = valor;
        const decorrido = (Date.now() - processingStartedAt) / 1000;
        if (valor < 100) shortenEtaDeadline(decorrido * ((100 - valor) / valor));
    }

    // --- Telas ---
    function showSection(secao) {
        [uploadSection, processingSection, resultSection, errorSection].forEach(s => {
            s.style.display = 'none';
        });
        secao.style.display = 'block';
        secao.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function showSuccess(data) {
        const arquivos = data.total_output_files || 0;
        resultFiles.textContent = arquivos;
        resultChars.textContent = (data.total_caracteres || 0).toLocaleString('pt-BR');

        const urls = data.download_urls || {};
        downloadPdfBtn.href = urls.pdf || '#';
        downloadMdBtn.href = urls.md || '#';

        const varios = arquivos > 1;
        downloadPdfBtn.querySelector('span').firstChild.nodeValue =
            varios ? 'Baixar PDFs pesquisáveis (ZIP)' : 'Baixar PDF pesquisável';
        downloadMdBtn.querySelector('span').firstChild.nodeValue =
            varios ? 'Baixar Markdown (ZIP)' : 'Baixar Markdown (.md)';

        pdfSizeHint.textContent = data.pdf_size_mb ? ` · ${data.pdf_size_mb} MB` : '';
        mdSizeHint.textContent = data.md_size_mb ? ` · ${data.md_size_mb} MB` : '';

        renderWarnings(data.warnings || []);
        showSection(resultSection);

        // Sem download automático: aqui são dois formatos, e escolher por conta
        // própria significaria baixar o que o usuário talvez não queira.
        showToast('Reconhecimento concluído. Escolha o formato para baixar.', 'success');
    }

    function renderWarnings(avisos) {
        resultWarningsList.innerHTML = '';
        if (!avisos.length) {
            resultWarnings.style.display = 'none';
            return;
        }
        avisos.forEach(aviso => {
            const item = document.createElement('li');
            item.textContent = aviso;
            resultWarningsList.appendChild(item);
        });
        resultWarnings.style.display = 'flex';
    }

    function showFailure(mensagem) {
        errorMessage.textContent = mensagem;
        showSection(errorSection);
        showToast('Ocorreu um erro no reconhecimento.', 'error');
    }

    // --- Reinício ---
    function resetApp() {
        stopPolling();
        stopEtaTicker();
        clearActiveJob();
        selectedFiles = [];
        processingStartedAt = null;
        lastProcessingProgress = 0;
        etaDeadline = null;
        ocrForm.reset();

        langCards.forEach(c => c.classList.remove('active'));
        if (langCards.length) langCards[0].classList.add('active');
        const radioPor = document.querySelector('input[name="idioma"][value="por"]');
        if (radioPor) radioPor.checked = true;

        submitBtn.disabled = true;
        submitBtn.classList.remove('loading');
        submitBtn.querySelector('span').textContent = 'Reconhecer texto';

        updateProgress(0, 'Iniciando...');
        uploadProgressFill.style.width = '0%';
        uploadProgressPercent.textContent = '0%';
        uploadProgressText.textContent = 'Preparando envio...';
        workflowEta.textContent = 'Tempo restante total: calculando...';
        uploadProgressContainer.style.display = 'block';
        processingProgressContainer.style.display = 'none';

        resultFiles.textContent = '—';
        resultChars.textContent = '—';
        downloadPdfBtn.href = '#';
        downloadMdBtn.href = '#';
        renderWarnings([]);
        renderFileList();
        showSection(uploadSection);
    }

    newOcrBtn.addEventListener('click', resetApp);
    retryBtn.addEventListener('click', resetApp);

    // --- Retomada após refresh / queda de conexão ---
    const ACTIVE_JOB_TTL_MS = 2 * 60 * 60 * 1000;

    async function attemptResume() {
        const ativo = getActiveJob();
        if (!ativo || !ativo.jobId) return;

        if (ativo.ts && (Date.now() - ativo.ts) > ACTIVE_JOB_TTL_MS) {
            clearActiveJob();
            return;
        }

        processingTitle.textContent = 'Reconectando...';
        uploadProgressContainer.style.display = 'none';
        processingProgressContainer.style.display = 'block';
        progressText.textContent = 'Retomando seu processamento...';
        workflowEta.textContent = 'Reconectando ao servidor...';
        showSection(processingSection);

        try {
            const response = await fetch(`/ocr/api/status/${ativo.jobId}/`);
            const data = await response.json();

            if (!response.ok) {
                clearActiveJob();
                showSection(uploadSection);
                return;
            }

            if (data.status === 'completed') {
                updateProgress(100, 'Reconhecimento concluído.');
                clearActiveJob();
                showSuccess(data);
            } else if (data.status === 'failed') {
                clearActiveJob();
                showFailure(data.error_message || 'Erro inesperado no servidor.');
            } else {
                processingTitle.textContent = 'Reconhecendo...';
                startEtaTicker();
                startPolling(ativo.jobId);
            }
        } catch (e) {
            processingTitle.textContent = 'Reconhecendo...';
            startPolling(ativo.jobId);
        }
    }

    attemptResume();
});
