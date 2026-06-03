/**
 * DIVISOR PDF — Frontend Application Logic
 * Gerencia a seleção de arquivos (Drag & Drop / Input), validações locais,
 * compressão opcional via Ghostscript, divisão opcional via pypdf,
 * pooling de progresso e download do arquivo ZIP resultante.
 */

document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements ---
    const uploadSection = document.getElementById('upload-section');
    const uploadForm = document.getElementById('upload-form');
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const fileList = document.getElementById('file-list');
    const fileItems = document.getElementById('file-items');
    const fileCountText = document.getElementById('file-count');
    const fileTotalSizeText = document.getElementById('file-total-size');
    const clearFilesBtn = document.getElementById('clear-files-btn');
    
    // Compression controls
    const compressControl = document.getElementById('compress-control');
    const compressOptCards = document.querySelectorAll('.compress-opt-card');
    const actionSwitchControl = document.getElementById('action-switch-control');
    const shouldSplitCheckbox = document.getElementById('should-split-checkbox');
    
    // Size estimation labels
    const estSizeNone = document.getElementById('est-size-none');
    const estSizeLow = document.getElementById('est-size-low');
    const estSizeMedium = document.getElementById('est-size-medium');
    const estSizeHigh = document.getElementById('est-size-high');

    // Division controls
    const sizeControl = document.getElementById('size-control');
    const maxSizeSlider = document.getElementById('max-size-slider');
    const maxSizeInput = document.getElementById('max-size-input');
    const sizeHint = document.getElementById('size-hint');
    const timeEstimate = document.getElementById('time-estimate');
    const timeEstimateTotal = document.getElementById('time-estimate-total');
    const submitBtn = document.getElementById('submit-btn');

    // Processing & progress UI
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

    // Results UI
    const resultSection = document.getElementById('result-section');
    const resultFiles = document.getElementById('result-files');
    const resultSize = document.getElementById('result-size');
    const downloadBtn = document.getElementById('download-btn');
    const newSplitBtn = document.getElementById('new-split-btn');
    const resultWarnings = document.getElementById('result-warnings');
    const resultWarningsList = document.getElementById('result-warnings-list');

    // Error UI
    const errorSection = document.getElementById('error-section');
    const errorMessage = document.getElementById('error-message');
    const retryBtn = document.getElementById('retry-btn');
    
    const toastContainer = document.getElementById('toast-container');

    // --- State Variables ---
    let selectedFiles = [];
    let pollingInterval = null;
    let etaTickInterval = null;
    let workflowEtaDeadline = null;
    let latestProcessingProgress = 0;
    let processingStartedAt = null;
    let lastProcessingProgress = 0;
    const MAX_FILE_SIZE_MB = Number(uploadForm.dataset.maxUploadSizeMb || 500);
    const MAX_TOTAL_SIZE_MB = Number(uploadForm.dataset.maxTotalUploadMb || 2048);

    // --- Toast Notifications ---
    function showToast(message, type = 'error') {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        toastContainer.appendChild(toast);

        // Remove toast com animação suave
        setTimeout(() => {
            toast.classList.add('toast-out');
            toast.addEventListener('animationend', () => toast.remove());
        }, 4000);
    }

    // --- Format Bytes helper ---
    function formatBytes(bytes, decimals = 2) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }

    function formatDuration(seconds) {
        if (!Number.isFinite(seconds) || seconds <= 0) return 'menos de 1s';

        const roundedSeconds = Math.max(1, Math.round(seconds));
        const minutes = Math.floor(roundedSeconds / 60);
        const remainingSeconds = roundedSeconds % 60;

        if (minutes <= 0) return `${remainingSeconds}s`;
        return `${minutes}min ${remainingSeconds.toString().padStart(2, '0')}s`;
    }

    function getTotalSelectedBytes() {
        return selectedFiles.reduce((acc, f) => acc + f.size, 0);
    }

    function getSelectedActions() {
        const selectedRadio = document.querySelector('input[name="compress_level"]:checked');
        return {
            compressLevel: selectedRadio ? selectedRadio.value : 'none',
            shouldSplit: shouldSplitCheckbox.checked,
        };
    }

    function estimateActionDurations(totalBytes, compressLevel, shouldSplit) {
        const totalMb = totalBytes / (1024 * 1024);
        const fileCount = Math.max(selectedFiles.length, 1);
        const durations = {
            setup: 2 + (fileCount * 1.5),
            compression: 0,
            split: 0,
        };

        if (compressLevel !== 'none') {
            const compressionFactor = {
                low: 0.8,
                medium: 1.2,
                high: 1.7,
            }[compressLevel] || 1.2;
            durations.compression = Math.max(3, totalMb * compressionFactor);
        }

        if (shouldSplit) {
            const maxSizeMb = Math.max(parseFloat(maxSizeInput.value) || 10, 0.1);
            const expectedParts = Math.max(fileCount, Math.ceil(totalMb / maxSizeMb));
            durations.split = Math.max(2, totalMb * 0.35) + (expectedParts * 0.8);
        }

        return durations;
    }

    function getWorkflowEstimate() {
        const { compressLevel, shouldSplit } = getSelectedActions();
        const totalBytes = getTotalSelectedBytes();
        const estimatedUploadSeconds = Math.max(1, (totalBytes / (1024 * 1024)) / 8);
        const durations = estimateActionDurations(totalBytes, compressLevel, shouldSplit);
        const totalSeconds = (
            estimatedUploadSeconds
            + durations.setup
            + durations.compression
            + durations.split
        );

        return {
            compressLevel,
            shouldSplit,
            totalSeconds,
            upload: estimatedUploadSeconds,
            setup: durations.setup,
            compression: durations.compression,
            split: durations.split,
        };
    }

    function updateInitialTimeEstimate() {
        if (selectedFiles.length === 0) {
            timeEstimate.style.display = 'none';
            return;
        }

        const estimate = getWorkflowEstimate();
        if (estimate.compressLevel === 'none' && !estimate.shouldSplit) {
            timeEstimate.style.display = 'none';
            return;
        }

        timeEstimateTotal.textContent = `~${formatDuration(estimate.totalSeconds)}`;
        timeEstimate.style.display = 'block';
    }

    function stopEtaTicker() {
        if (etaTickInterval) {
            clearInterval(etaTickInterval);
            etaTickInterval = null;
        }
        workflowEtaDeadline = null;
    }

    function startEtaTicker() {
        if (etaTickInterval) clearInterval(etaTickInterval);

        etaTickInterval = setInterval(() => {
            updateWorkflowEta();
        }, 1000);
    }

    function setWorkflowEtaDeadline(seconds) {
        workflowEtaDeadline = Date.now() + (Math.max(seconds, 1) * 1000);
        updateWorkflowEta();
    }

    function shortenWorkflowEtaDeadline(seconds) {
        if (!workflowEtaDeadline) return;

        const proposedDeadline = Date.now() + (Math.max(seconds, 1) * 1000);
        if (proposedDeadline < workflowEtaDeadline) {
            workflowEtaDeadline = proposedDeadline;
            updateWorkflowEta();
        }
    }

    function setWorkflowEtaFromProgress(seconds) {
        if (!workflowEtaDeadline) return;

        const proposedDeadline = Date.now() + (Math.max(seconds, 1) * 1000);
        const currentRemainingSeconds = Math.max((workflowEtaDeadline - Date.now()) / 1000, 0);

        if (proposedDeadline < workflowEtaDeadline || currentRemainingSeconds <= 5) {
            workflowEtaDeadline = proposedDeadline;
            updateWorkflowEta();
        }
    }

    function updateWorkflowEta() {
        if (!workflowEtaDeadline) {
            workflowEta.textContent = 'Tempo restante total: calculando...';
            return;
        }

        const remainingSeconds = Math.max((workflowEtaDeadline - Date.now()) / 1000, 0);
        if (remainingSeconds <= 0) {
            workflowEta.textContent = latestProcessingProgress >= 95
                ? 'Tempo restante total: finalizando...'
                : 'Tempo restante total: recalculando...';
            return;
        }

        workflowEta.textContent = `Tempo restante total: ${formatDuration(remainingSeconds)}`;
    }

    // --- Slider & Input Sync ---
    function syncSizeLimit(value) {
        const numValue = parseFloat(value);
        if (isNaN(numValue)) return;

        maxSizeSlider.value = Math.min(Math.max(numValue, 0.5), 100);
        maxSizeInput.value = numValue;
        sizeHint.innerHTML = `Cada PDF resultante terá no máximo <strong>${numValue.toFixed(1)} MB</strong>`;
        updateInitialTimeEstimate();
    }

    maxSizeSlider.addEventListener('input', (e) => syncSizeLimit(e.target.value));
    maxSizeInput.addEventListener('input', (e) => {
        let value = parseFloat(e.target.value);
        if (value > MAX_FILE_SIZE_MB) value = MAX_FILE_SIZE_MB;
        if (value < 0.1) value = 0.1;
        
        maxSizeSlider.value = Math.min(Math.max(value, 0.5), 100);
        sizeHint.innerHTML = `Cada PDF resultante terá no máximo <strong>${value.toFixed(1)} MB</strong>`;
        updateInitialTimeEstimate();
    });

    // --- Compression Cards Selection ---
    compressOptCards.forEach(card => {
        card.addEventListener('click', function(e) {
            // Previne propagação indesejada se clicar diretamente no rádio
            if (e.target.tagName === 'INPUT') return;

            compressOptCards.forEach(c => c.classList.remove('active'));
            this.classList.add('active');

            const radio = this.querySelector('input[type="radio"]');
            if (radio) {
                radio.checked = true;
                // Dispara evento de mudança para recalcular interface se necessário
                radio.dispatchEvent(new Event('change', { bubbles: true }));
            }
            
            updateActionButtonText();
            updateInitialTimeEstimate();
        });

        // Ouvir mudanças diretas do rádio ocultado
        const radio = card.querySelector('input[type="radio"]');
        if (radio) {
            radio.addEventListener('change', () => {
                compressOptCards.forEach(c => c.classList.remove('active'));
                card.classList.add('active');
                updateActionButtonText();
                updateInitialTimeEstimate();
            });
        }
    });

    // --- Action Switch Listener ---
    shouldSplitCheckbox.addEventListener('change', function() {
        if (this.checked) {
            sizeControl.style.display = 'block';
            sizeControl.style.animation = 'fade-in-up 0.3s ease-out';
        } else {
            sizeControl.style.display = 'none';
        }
        updateActionButtonText();
        updateInitialTimeEstimate();
    });

    // --- Update Main Button text based on selections ---
    function updateActionButtonText() {
        const selectedRadio = document.querySelector('input[name="compress_level"]:checked');
        const compressLevel = selectedRadio ? selectedRadio.value : 'none';
        const isSplitChecked = shouldSplitCheckbox.checked;

        let btnText = 'Processar PDFs';
        let btnIcon = `<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>`;

        if (compressLevel !== 'none' && isSplitChecked) {
            btnText = 'Comprimir & Dividir PDFs';
        } else if (compressLevel !== 'none') {
            btnText = 'Comprimir PDFs';
            btnIcon = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M4 14h6v6H4zm10 0h6v6h-6zM4 4h6v6H4zm10 0h6v6h-6z"/>
            </svg>`;
        } else if (isSplitChecked) {
            btnText = 'Dividir PDFs';
            btnIcon = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <line x1="9" y1="3" x2="9" y2="21"/>
            </svg>`;
        }

        submitBtn.innerHTML = `${btnIcon}<span>${btnText}</span>`;

        // Habilitar ou desabilitar o botão se nenhuma ação estiver selecionada
        const hasAction = (compressLevel !== 'none' || isSplitChecked);
        if (selectedFiles.length > 0 && hasAction) {
            submitBtn.disabled = false;
        } else {
            submitBtn.disabled = true;
        }
        updateInitialTimeEstimate();
    }

    // --- Drag & Drop ---
    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            dropZone.classList.add('drag-over');
        }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            dropZone.classList.remove('drag-over');
        }, false);
    });

    dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = Array.from(dt.files);
        handleFiles(files);
    });

    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            fileInput.click();
        }
    });

    fileInput.addEventListener('change', (e) => {
        const files = Array.from(e.target.files);
        handleFiles(files);
    });

    // --- File Handling & Validation ---
    function handleFiles(files) {
        const pdfFiles = files.filter(file => {
            const isPdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
            if (!isPdf) {
                showToast(`O arquivo "${file.name}" não é um PDF válido.`);
            }
            return isPdf;
        });

        if (pdfFiles.length === 0) return;

        // Validar tamanho
        let totalCurrentSize = selectedFiles.reduce((acc, f) => acc + f.size, 0);

        for (const file of pdfFiles) {
            // Verifica duplicidade por nome e tamanho
            const isDuplicate = selectedFiles.some(f => f.name === file.name && f.size === file.size);
            if (isDuplicate) continue;

            const sizeMb = file.size / (1024 * 1024);
            if (sizeMb > MAX_FILE_SIZE_MB) {
                showToast(`O arquivo "${file.name}" excede o limite de ${MAX_FILE_SIZE_MB} MB.`);
                continue;
            }

            if ((totalCurrentSize + file.size) / (1024 * 1024) > MAX_TOTAL_SIZE_MB) {
                showToast(`O tamanho total acumulado excede o limite de ${MAX_TOTAL_SIZE_MB} MB.`);
                break;
            }

            selectedFiles.push(file);
            totalCurrentSize += file.size;
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

    function updateSizeEstimates(totalBytes) {
        const totalMb = totalBytes / (1024 * 1024);
        
        estSizeNone.textContent = `Original (${totalMb.toFixed(1)} MB)`;
        estSizeLow.textContent = `~ ${(totalMb * 0.8).toFixed(1)} MB (300 DPI)`;
        estSizeMedium.textContent = `~ ${(totalMb * 0.5).toFixed(1)} MB (150 DPI)`;
        estSizeHigh.textContent = `~ ${(totalMb * 0.2).toFixed(1)} MB (72 DPI)`;
    }

    function renderFileList() {
        fileItems.innerHTML = '';

        if (selectedFiles.length === 0) {
            fileList.style.display = 'none';
            compressControl.style.display = 'none';
            actionSwitchControl.style.display = 'none';
            sizeControl.style.display = 'none';
            timeEstimate.style.display = 'none';
            submitBtn.style.display = 'none';
            submitBtn.disabled = true;
            fileInput.value = '';
            return;
        }

        selectedFiles.forEach((file, index) => {
            const li = document.createElement('li');
            li.className = 'file-item';

            li.innerHTML = `
                <div class="file-item-icon">PDF</div>
                <div class="file-item-info">
                    <div class="file-item-name" title="${file.name}">${file.name}</div>
                    <div class="file-item-size">${formatBytes(file.size)}</div>
                </div>
                <button type="button" class="file-item-remove" aria-label="Remover arquivo">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="18" y1="6" x2="6" y2="18"/>
                        <line x1="6" y1="6" x2="18" y2="12"/>
                        <polyline points="3 6 5 6 21 6"/>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                    </svg>
                </button>
            `;

            li.querySelector('.file-item-remove').addEventListener('click', () => removeFile(index));
            fileItems.appendChild(li);
        });

        // Atualizar Sumário
        const totalSize = selectedFiles.reduce((acc, f) => acc + f.size, 0);
        fileCountText.textContent = `${selectedFiles.length} ${selectedFiles.length === 1 ? 'arquivo' : 'arquivos'}`;
        fileTotalSizeText.textContent = formatBytes(totalSize);

        // Atualiza estimativas do Ghostscript com base no tamanho original carregado
        updateSizeEstimates(totalSize);

        // Exibe seções de configuração e controle de forma fluida
        fileList.style.display = 'block';
        compressControl.style.display = 'block';
        actionSwitchControl.style.display = 'block';
        
        if (shouldSplitCheckbox.checked) {
            sizeControl.style.display = 'block';
        } else {
            sizeControl.style.display = 'none';
        }
        
        updateActionButtonText();
        updateInitialTimeEstimate();
        submitBtn.style.display = 'inline-flex';
    }

    // --- Form Submission ---
    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        if (selectedFiles.length === 0) {
            showToast('Selecione pelo menos um arquivo PDF.');
            return;
        }

        const selectedRadio = document.querySelector('input[name="compress_level"]:checked');
        const compressLevel = selectedRadio ? selectedRadio.value : 'none';
        const isSplitChecked = shouldSplitCheckbox.checked;

        // Validação mínima de seleção
        if (compressLevel === 'none' && !isSplitChecked) {
            showToast('Selecione pelo menos uma ação para prosseguir: Comprimir ou Dividir.');
            return;
        }

        const formData = new FormData();
        selectedFiles.forEach(file => {
            formData.append('files', file);
        });
        
        formData.append('compress_level', compressLevel);
        formData.append('should_split', isSplitChecked ? 'true' : 'false');

        if (isSplitChecked) {
            const max_size_mb = parseFloat(maxSizeInput.value);
            if (isNaN(max_size_mb) || max_size_mb < 0.1) {
                showToast('Informe um tamanho máximo de divisão válido.');
                return;
            }
            if (max_size_mb > MAX_FILE_SIZE_MB) {
                showToast(`O tamanho máximo de divisão não pode exceder ${MAX_FILE_SIZE_MB} MB.`);
                return;
            }
            formData.append('max_size_mb', max_size_mb);
        }

        // Get CSRF Token
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
        
        // Visual Transition to Sending
        submitBtn.disabled = true;
        submitBtn.classList.add('loading');
        
        const originalText = submitBtn.querySelector('span').textContent;
        submitBtn.querySelector('span').textContent = 'Enviando arquivos...';

        // Preparar interface de progresso
        processingTitle.textContent = 'Enviando...';
        uploadProgressFill.style.width = '0%';
        uploadProgressPercent.textContent = '0%';
        uploadProgressText.textContent = 'Iniciando upload dos arquivos...';
        uploadProgressContainer.style.display = 'block';
        processingProgressContainer.style.display = 'none';
        stopEtaTicker();
        setWorkflowEtaDeadline(getWorkflowEstimate().totalSeconds);
        startEtaTicker();

        // Mostra a seção de processamento/envio imediatamente
        showSection(processingSection);

        // Upload usando XMLHttpRequest para suporte a progresso real
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload/', true);
        xhr.setRequestHeader('X-CSRFToken', csrfToken);
        const uploadStartedAt = Date.now();

        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) {
                const percentComplete = Math.round((e.loaded / e.total) * 100);
                uploadProgressFill.style.width = `${percentComplete}%`;
                uploadProgressPercent.textContent = `${percentComplete}%`;
                
                const loadedMb = (e.loaded / (1024 * 1024)).toFixed(1);
                const totalMb = (e.total / (1024 * 1024)).toFixed(1);
                uploadProgressText.textContent = `Enviando arquivos (${loadedMb} MB de ${totalMb} MB)...`;

                if (e.loaded > 0 && percentComplete < 100) {
                    const elapsedSeconds = (Date.now() - uploadStartedAt) / 1000;
                    const uploadRate = e.loaded / Math.max(elapsedSeconds, 0.1);
                    const remainingBytes = Math.max(e.total - e.loaded, 0);
                    const remainingSeconds = remainingBytes / uploadRate;
                    const estimate = getWorkflowEstimate();
                    const remainingProcessingEstimate = (
                        estimate.setup + estimate.compression + estimate.split
                    );
                    shortenWorkflowEtaDeadline(remainingSeconds + remainingProcessingEstimate);
                }
            }
        });

        xhr.onload = function() {
            if (xhr.status >= 200 && xhr.status < 300) {
                try {
                    const data = JSON.parse(xhr.responseText);
                    
                    // Envio finalizado
                    uploadProgressFill.style.width = '100%';
                    uploadProgressPercent.textContent = '100%';
                    uploadProgressText.textContent = 'Upload concluído!';

                    // Exibe a Fase 2 (Processamento no Servidor)
                    processingTitle.textContent = 'Processando...';
                    processingProgressContainer.style.display = 'block';
                    progressFill.style.width = '0%';
                    progressPercent.textContent = '0%';
                    progressText.textContent = 'Iniciando processamento no servidor...';

                    // Inicia polling de status
                    startPolling(data.job_id, compressLevel !== 'none', isSplitChecked);
                } catch (err) {
                    handleUploadError('Resposta inválida do servidor.');
                }
            } else {
                try {
                    const data = JSON.parse(xhr.responseText);
                    handleUploadError(data.error || 'Ocorreu um erro no upload.');
                } catch (err) {
                    handleUploadError('Falha no upload dos arquivos.');
                }
            }
        };

        xhr.onerror = function() {
            handleUploadError('Erro de conexão durante o upload.');
        };

        function handleUploadError(errorMessage) {
            submitBtn.disabled = false;
            submitBtn.classList.remove('loading');
            submitBtn.querySelector('span').textContent = originalText;
            
            // Retorna para tela anterior e exibe erro
            showSection(uploadSection);
            showToast(errorMessage);
        }

        xhr.send(formData);
    });

    // --- Polling Logic ---
    function startPolling(jobId, hasCompression, hasSplit) {
        processingStartedAt = Date.now();
        lastProcessingProgress = 0;
        updateProgress(0, 'Iniciando processamento...');
        
        pollingInterval = setInterval(async () => {
            try {
                const response = await fetch(`/api/status/${jobId}/`);
                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.error || 'Erro ao obter status do processamento.');
                }

                if (data.status === 'processing' || data.status === 'pending') {
                    let msg = 'Processando arquivos...';
                    
                    if (data.status === 'processing') {
                        if (hasCompression && data.progress <= 45) {
                            msg = 'Comprimindo PDFs via Ghostscript (otimizando imagens)...';
                        } else if (hasSplit) {
                            msg = 'Dividindo páginas do PDF de acordo com o tamanho limite...';
                        } else {
                            msg = 'Finalizando compressão...';
                        }
                    } else {
                        msg = 'Aguardando na fila do servidor...';
                    }
                    
                    updateProgress(data.progress || 0, msg);
                } 
                
                else if (data.status === 'completed') {
                    stopPolling();
                    updateProgress(100, 'Processamento concluído.');
                    stopEtaTicker();
                    workflowEta.textContent = 'Tempo restante total: concluído';
                    showSuccess(data);
                } 
                
                else if (data.status === 'failed') {
                    stopPolling();
                    stopEtaTicker();
                    showFailure(data.error_message || 'Erro inesperado no servidor.');
                }

            } catch (error) {
                stopPolling();
                stopEtaTicker();
                showFailure(error.message);
            }
        }, 1500);
    }

    function stopPolling() {
        if (pollingInterval) {
            clearInterval(pollingInterval);
            pollingInterval = null;
        }
    }

    function updateProgress(percent, message) {
        const normalizedPercent = Math.min(Math.max(Number(percent) || 0, 0), 100);
        progressFill.style.width = `${normalizedPercent}%`;
        progressPercent.textContent = `${normalizedPercent}%`;
        progressText.textContent = message;
        latestProcessingProgress = normalizedPercent;

        if (!processingStartedAt || normalizedPercent <= 0 || normalizedPercent <= lastProcessingProgress) {
            return;
        }

        lastProcessingProgress = normalizedPercent;
        const elapsedSeconds = (Date.now() - processingStartedAt) / 1000;
        const remainingSeconds = elapsedSeconds * ((100 - normalizedPercent) / normalizedPercent);
        if (normalizedPercent < 100) {
            setWorkflowEtaFromProgress(remainingSeconds);
        }
    }

    // --- Show Sections helper ---
    function showSection(sectionToShow) {
        [uploadSection, processingSection, resultSection, errorSection].forEach(section => {
            section.style.display = 'none';
        });
        
        sectionToShow.style.display = 'block';
        sectionToShow.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // --- Result UI triggers ---
    function showSuccess(data) {
        resultFiles.textContent = data.total_output_files;
        resultSize.textContent = `${data.zip_size_mb.toFixed(2)} MB`;
        downloadBtn.href = data.download_url;
        renderWarnings(data.warnings || []);

        // Atualizar rótulos e botão dinamicamente para arquivo único vs múltiplos
        const isSingleFile = data.total_output_files === 1;
        const sizeLabel = resultSection.querySelector('.stat:nth-child(2) .stat-label');
        const downloadSpan = downloadBtn.querySelector('span');

        if (isSingleFile) {
            if (sizeLabel) sizeLabel.textContent = 'Tamanho do PDF';
            if (downloadSpan) downloadSpan.textContent = 'Baixar PDF';
        } else {
            if (sizeLabel) sizeLabel.textContent = 'Tamanho do ZIP';
            if (downloadSpan) downloadSpan.textContent = 'Baixar ZIP';
        }

        // Atualizar título do resultado com base nas ações
        const resultHeaderH2 = resultSection.querySelector('.card-header h2');
        const selectedRadio = document.querySelector('input[name="compress_level"]:checked');
        const compressLevel = selectedRadio ? selectedRadio.value : 'none';
        const isSplitChecked = shouldSplitCheckbox.checked;
        
        if (compressLevel !== 'none' && isSplitChecked) {
            resultHeaderH2.textContent = 'Compressão & Divisão Concluídas!';
        } else if (compressLevel !== 'none') {
            resultHeaderH2.textContent = 'Compressão Concluída!';
        } else {
            resultHeaderH2.textContent = 'Divisão Concluída!';
        }

        showSection(resultSection);
        showToast('Processamento concluído com sucesso!', 'success');

        // Dispara o download automaticamente após a transição
        setTimeout(() => {
            downloadBtn.click();
        }, 500);
    }

    function renderWarnings(warnings) {
        resultWarningsList.innerHTML = '';

        if (!warnings.length) {
            resultWarnings.style.display = 'none';
            return;
        }

        warnings.forEach((warning) => {
            const item = document.createElement('li');
            item.textContent = warning;
            resultWarningsList.appendChild(item);
        });

        resultWarnings.style.display = 'block';
    }

    function showFailure(messageText) {
        errorMessage.textContent = messageText;
        showSection(errorSection);
        showToast('Ocorreu um erro no processamento.', 'error');
    }

    // --- Reset Flow ---
    function resetApp() {
        stopPolling();
        stopEtaTicker();
        selectedFiles = [];
        latestProcessingProgress = 0;
        processingStartedAt = null;
        lastProcessingProgress = 0;
        uploadForm.reset();
        
        // Reset compression radios
        compressOptCards.forEach(c => c.classList.remove('active'));
        compressOptCards[0].classList.add('active'); // none active
        const noCompressionRadio = document.querySelector('input[name="compress_level"][value="none"]');
        if (noCompressionRadio) noCompressionRadio.checked = true;

        // Reset split checkbox
        shouldSplitCheckbox.checked = true;
        timeEstimate.style.display = 'none';
        renderFileList();

        // Reset submit button state
        submitBtn.disabled = true;
        submitBtn.classList.remove('loading');
        updateActionButtonText();
        
        // Reset progress bar
        updateProgress(0, 'Iniciando...');
        uploadProgressFill.style.width = '0%';
        uploadProgressPercent.textContent = '0%';
        uploadProgressText.textContent = 'Preparando envio...';
        workflowEta.textContent = 'Tempo restante total: calculando...';
        uploadProgressContainer.style.display = 'block';
        processingProgressContainer.style.display = 'none';

        // Reset result state
        resultFiles.textContent = '—';
        resultSize.textContent = '—';
        downloadBtn.href = '#';
        renderWarnings([]);

        showSection(uploadSection);
    }

    newSplitBtn.addEventListener('click', resetApp);
    retryBtn.addEventListener('click', resetApp);
});
