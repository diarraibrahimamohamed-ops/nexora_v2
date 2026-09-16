async function analyzeResistance() {
    const organismType = getOrganismType();
    const antibiotic = document.getElementById('antibioticSelect')?.value;
    const resultsContainer = document.getElementById('resistanceResults');
    if (!resultsContainer) return;

    resultsContainer.innerHTML = '<div class="text-gray-300">Analyse génétique en cours…</div>';
    const token = localStorage.getItem('token') || sessionStorage.getItem('token');

    try {
        const sequence = typeof currentSequence === 'string' ? currentSequence : '';
        const response = await fetch(`${CONFIG.API.BASE_URL}/analysis/amr`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { 'Authorization': `Bearer ${token}` } : {})
            },
            body: JSON.stringify({ sequence, organism_type: organismType === 'human' ? 'virus' : 'bacterium' })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Analyse AMR indisponible');
        renderAMRResult(data, antibiotic);
        window.lastAMRResult = data;
        analyzeResistanceProfile();
    } catch (error) {
        console.error('[AMR]', error);
        resultsContainer.innerHTML = '<div class="text-red-300">Analyse AMR indisponible. Aucun pourcentage synthétique n\'est affiché.</div>';
    }
}

function renderAMRResult(data, antibiotic) {
    const resultsContainer = document.getElementById('resistanceResults');
    if (!resultsContainer) return;
    if (data.applicability === 'not_applicable') {
        resultsContainer.innerHTML = `<div class="antibiotic-result human"><div><div class="font-bold">Antibiotiques</div><div class="text-sm">${data.interpretation}</div></div><div class="resistance-indicator human">N/A</div></div>`;
        return;
    }

    const hits = Array.isArray(data.hits) ? data.hits : [];
    const relevant = antibiotic ? hits.filter(h => !data.antibiotic_classes || !data.antibiotic_classes[antibiotic] || true) : hits;
    const evidence = data.evidence_level || 'none';
    const title = antibiotic ? antibiotic.charAt(0).toUpperCase() + antibiotic.slice(1) : 'Profil AMR';
    const hitHTML = relevant.length ? relevant.map(hit => `
        <div class="mt-2 p-3 bg-gray-900 rounded">
            <div><strong>${escapeHtml(hit.gene_symbol || 'Déterminant')}</strong> <span class="text-xs">(${escapeHtml(hit.method || 'méthode inconnue')})</span></div>
            <div class="text-xs text-gray-400">Identité: ${formatPct(hit.identity_pct)} · Couverture: ${formatPct(hit.coverage_pct)}</div>
        </div>`).join('') : '<div class="text-sm text-gray-400">Aucun déterminant AMR détecté par le moteur disponible.</div>';

    resultsContainer.innerHTML = `
        <div class="antibiotic-result low-resistance">
            <div style="width:100%">
                <div class="font-bold">${title}</div>
                <div class="text-sm">Niveau de preuve génotypique: <strong>${escapeHtml(evidence)}</strong></div>
                <div class="text-sm mt-1">${escapeHtml(data.interpretation || '')}</div>
                ${hitHTML}
                <div class="text-xs text-gray-500 mt-2">Aucun phénotype S/I/R ni probabilité de résistance n'est déduit ici.</div>
            </div>
        </div>`;
}

function formatPct(value) { return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}%` : 'n/d'; }
function escapeHtml(value) { const d=document.createElement('div'); d.textContent=String(value ?? ''); return d.innerHTML; }

function analyzeResistanceProfile() {
    if (!resistanceChart) return;
    const data = window.lastAMRResult;
    // Plot a deterministic evidence presence (0/1), not a resistance percentage.
    const value = data && Array.isArray(data.hits) && data.hits.length ? 1 : 0;
    resistanceChart.data.datasets[0].data = [value, value, value, value, value, value];
    if (resistanceChart.options?.scales?.y) {
        resistanceChart.options.scales.y.min = 0;
        resistanceChart.options.scales.y.max = 1;
        resistanceChart.options.scales.y.ticks = { callback: v => v === 1 ? 'Déterminant détecté' : 'Aucun détecté' };
    }
    resistanceChart.update();
}

function getOrganismType() {
    const radios = document.getElementsByName('organismType');
    for (const radio of radios) {
if (radio.checked) return radio.value;
    }
    return 'microbe'; // valeur par défaut
}


// Classe CSS selon le niveau de résistance
function getResistanceClass(level) {
    if (level >= 70) return 'high-resistance';
    if (level >= 40) return 'medium-resistance';
    return 'low-resistance';
}

 function clearSequences() {
    document.getElementById('sequenceInput').value = '';
    document.getElementById('referenceInput').value = '';
    document.getElementById('sequenceViewer').innerHTML = '';
    document.getElementById('mutationAlerts').innerHTML = '';
    
    // Clear displays
    ['dnaTemplate', 'rnaTranscript', 'matureRNA', 'codonSequence', 'proteinSequence', 'aminoAcidNames'].forEach(id => {
        const element = document.getElementById(id);
        if (element) element.innerHTML = '';
    });
    
    // Reset statistics
    document.getElementById('seqLength').textContent = '0';
    document.getElementById('gcContent').textContent = '0%';
    document.getElementById('mutationCount').textContent = '0';
    document.getElementById('similarity').textContent = '0%';
    
    // Reset protein properties
    document.getElementById('proteinMass').textContent = '0';
    document.getElementById('proteinPI').textContent = '0';
    document.getElementById('hydrophobicity').textContent = '0';
    document.getElementById('secondaryStructure').textContent = '0%';
    
    // Reset charts
    if (nucleotideChart) {
        nucleotideChart.data.datasets[0].data = [0, 0, 0, 0];
        nucleotideChart.update();
    }
    if (rnaChart) {
        rnaChart.data.datasets[0].data = [0, 0, 0, 0];
        rnaChart.update();
    }
    if (aminoAcidChart) {
        aminoAcidChart.data.labels = [];
        aminoAcidChart.data.datasets[0].data = [];
        aminoAcidChart.update();
    }
    
    // Reset counters
    updateCounters(true);
}

function updateCounters(reset = false) {
    // Vérifier que le DOM est prêt avant de continuer
    if (!document.body) return;

    if (reset) {
        const seqEl = document.getElementById('sequencesAnalyzed');
        const mutEl = document.getElementById('mutationsDetected');
        const resEl = document.getElementById('resistanceLevel');
        const protEl = document.getElementById('proteinsAnalyzed');
        if (seqEl) seqEl.textContent = '0';
        if (mutEl) mutEl.textContent = '0';
        if (resEl) resEl.textContent = '0%';
        if (protEl) protEl.textContent = '0';
        return;
    }

    const mutEl = document.getElementById('mutationsDetected');
    if (mutEl) mutEl.textContent = mutations.length;

    const resEl = document.getElementById('resistanceLevel');
    if (resEl) resEl.textContent = window.lastAMRResult?.hits?.length ? 'Déterminants détectés' : 'Aucun déterminant détecté';

    const protEl = document.getElementById('proteinsAnalyzed');
    if (protEl) protEl.textContent = currentProtein ? currentProtein.length : 0;
}

// 3D controls
function rotateDNA() {
    if (dnaModel) {
        dnaModel.rotation.x += 0.5;
        dnaModel.rotation.y += 0.5;
    }
}

function zoomDNA() {
    if (dnaCamera) {
        dnaCamera.position.z = dnaCamera.position.z > 5 ? 5 : 15;
    }
}

function rotateProtein() {
    if (proteinModel) {
        proteinModel.rotation.x += 0.5;
        proteinModel.rotation.y += 0.5;
    }
}

function resetProtein() {
    if (proteinModel) {
        proteinModel.rotation.set(0, 0, 0);
    }
    if (proteinCamera) {
        proteinCamera.position.set(0, 0, 10);
    }
}

// Data stream
function startDataStream() {
    const dataStream = document.getElementById('dataStream');
    if (!dataStream) return;
    
    const messages = [
        'Initialisation du système...♣♣♣',
        'Chargement des bases de données...♣♣♣',
        'Analyse de séquence en cours...♣♣♣',
        'Détection de mutations...♣♣♣',
        'Transcription ADN → ARN...♣♣♣',
        'Traduction ARN → Protéine...♣♣♣',
        'Calcul des propriétés protéiques...♣♣♣',
        'Analyse de résistance...♣♣♣',
        'Génération de modèles 3D...♣♣♣',
        'Mise à jour des graphiques...♣♣♣',
        'Validation des résultats...♣♣♣',
        'Analyse terminée avec succès...♣♣♣'
    ];
    
    let messageIndex = 0;
    
    setInterval(() => {
        const timestamp = new Date().toLocaleTimeString();
        const message = messages[messageIndex % messages.length];
        
        const logEntry = document.createElement('div');
        logEntry.textContent = `[${timestamp}] ${message}`;
        logEntry.className = 'fade-in';
        
        dataStream.appendChild(logEntry);
        dataStream.scrollTop = dataStream.scrollHeight;
        
        // Limit messages
        if (dataStream.children.length > 15) {
            dataStream.removeChild(dataStream.firstChild);
        }
        
        messageIndex++;
    }, 2000);
}

// Floating particles
function createFloatingParticles() {
    const particleCount = 30;
    
    for (let i = 0; i < particleCount; i++) {
        const particle = document.createElement('div');
        particle.className = 'floating-particle';
        particle.style.left = Math.random() * 100 + '%';
        particle.style.top = Math.random() * 100 + '%';
        particle.style.animationDelay = Math.random() * 6 + 's';
        particle.style.animationDuration = (Math.random() * 4 + 4) + 's';
        document.body.appendChild(particle);
    }
}

// Resize handler
window.addEventListener('resize', function() {
    const dnaContainer = document.getElementById('dnaVisualization');
    const proteinContainer = document.getElementById('proteinVisualization');
    
    if (dnaRenderer && dnaContainer) {
        dnaRenderer.setSize(dnaContainer.clientWidth, dnaContainer.clientHeight);
        dnaCamera.aspect = dnaContainer.clientWidth / Math.max(dnaContainer.clientHeight, 1);
        dnaCamera.updateProjectionMatrix();
    }
    
    if (proteinRenderer && proteinContainer) {
        proteinRenderer.setSize(proteinContainer.clientWidth, proteinContainer.clientHeight);
        proteinCamera.aspect = proteinContainer.clientWidth / Math.max(proteinContainer.clientHeight, 1);
        proteinCamera.updateProjectionMatrix();
    }
});



function updateAIAnalysisStatus() {
    const statusContainer = document.getElementById('aiAnalysisStatus');
    if (!statusContainer) return;
    const hasSequence = typeof currentSequence === 'string' && currentSequence.length > 0;
    const hasMutations = Array.isArray(mutations) && mutations.length > 0;
    const hasProtein = typeof currentProtein === 'string' && currentProtein.length > 0;
    
    const statuses = [
{ condition: hasSequence, text: 'Séquence ADN analysée', icon: 'check', color: 'green' },
{ condition: hasMutations, text: `${mutations.length} mutations détectées`, icon: hasMutations ? 'exclamation-triangle' : 'circle', color: hasMutations ? 'yellow' : 'gray' },
{ condition: hasProtein, text: 'Protéine traduite', icon: hasProtein ? 'check' : 'circle', color: hasProtein ? 'green' : 'gray' },
{ condition: window.currentAnalysisId, text: 'Analyse sauvegardée', icon: window.currentAnalysisId ? 'check' : 'circle', color: window.currentAnalysisId ? 'green' : 'gray' }
    ];
    
    const html = statuses.map(status => `
<div class="flex items-center">
    <i class="fas fa-${status.icon} text-${status.color}-400 mr-2"></i>
    <span class="${status.condition ? 'text-white' : 'text-gray-400'}">${status.text}</span>
</div>
    `).join('');
    
    statusContainer.innerHTML = html;
}

// ==========================================
// FONCTIONS UTILITAIRES
// ==========================================

