function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `fixed top-4 right-4 z-50 p-4 rounded-lg shadow-lg max-w-sm transform transition-all duration-500 ${getNotificationClass(type)}`;
    notification.style.transform = 'translateX(100%)';
    
    notification.innerHTML = `
<div class="flex items-center">
    <i class="${getNotificationIcon(type)} mr-3"></i>
    <span class="flex-1">${message}</span>
    <button onclick="this.parentElement.parentElement.remove()" class="ml-2 text-white hover:text-gray-300">
        <i class="fas fa-times"></i>
    </button>
</div>
    `;
    
    document.body.appendChild(notification);
    
    // Animation d'entrée
    setTimeout(() => {
notification.style.transform = 'translateX(0)';
    }, 100);
    
    // Auto-suppression
    setTimeout(() => {
if (notification.parentElement) {
    notification.style.transform = 'translateX(100%)';
    setTimeout(() => notification.remove(), 500);
}
    }, 5000);
}

function getNotificationClass(type) {
    const classes = {
'success': 'bg-green-600 border-l-4 border-green-400',
'error': 'bg-red-600 border-l-4 border-red-400',
'warning': 'bg-yellow-600 border-l-4 border-yellow-400',
'info': 'bg-blue-600 border-l-4 border-blue-400'
    };
    return classes[type] || classes.info;
}

function getNotificationIcon(type) {
    const icons = {
'success': 'fas fa-check-circle',
'error': 'fas fa-exclamation-circle',
'warning': 'fas fa-exclamation-triangle',
'info': 'fas fa-info-circle'
    };
    return icons[type] || icons.info;
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function createElementFromHTML(htmlString) {
    const div = document.createElement('div');
    div.innerHTML = htmlString.trim();
    return div.firstChild;
}

function exportInterpretation(type, data) {
    const parsedData = typeof data === 'string' ? JSON.parse(data.replace(/&quot;/g, '"')) : data;
    
    const exportData = {
type: formatInterpretationType(type),
interpretation: parsedData.interpretation,
confidence_score: parsedData.confidence_score,
generated_at: new Date().toISOString(),
analysis_id: window.currentAnalysisId
    };
    
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.href = url;
    a.download = `interpretation_${type}_${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    
    showNotification('Interprétation exportée', 'success');
}

// ==========================================
// AMÉLIORATIONS 3D ET VISUALISATIONS
// ==========================================

function update3DModels() {
    return new Promise((resolve) => {
createDNAModel();
createProteinModel();
updateComplexityChart();
resolve();
    });
}

function updateComplexityChart() {
    if (!currentSequence) return;

    // Vérifier si le container existe (utiliser dnaVisualization au lieu de dna)
    const dnaVizContainer = document.getElementById('dnaVisualization');
    if (!dnaVizContainer) {
console.warn('[updateComplexityChart] Container #dnaVisualization not found, skipping chart creation');
return;
    }

    // Analyser la complexité de la séquence
    const windowSize = 50;
    const complexityData = [];
    const positions = [];

    for (let i = 0; i <= currentSequence.length - windowSize; i += 10) {
const window = currentSequence.substring(i, i + windowSize);
const complexity = calculateSequenceComplexity(window);
complexityData.push(complexity);
positions.push(i + windowSize / 2);
    }

    // Créer ou mis à jour le graphique de complexité
    if (!complexityChart) {
const ctx = document.createElement('canvas');
ctx.id = 'complexityChart';

const container = document.createElement('div');
container.className = 'hologram p-4 mt-6';
container.innerHTML = `
    <h3 class="text-lg font-bold mb-3 text-cyan-400">
        <i class="fas fa-chart-line mr-2"></i>
        Complexité de la Séquence
    </h3>
    <div class="chart-container">
    </div>
`;

const chartContainer = container.querySelector('.chart-container');
if (!chartContainer) {
    console.error('[updateComplexityChart] Container .chart-container not found');
    return;
}
chartContainer.appendChild(ctx);

const dnaContainer = dnaVizContainer.parentElement;
if (!dnaContainer) {
    console.error('[updateComplexityChart] Parent du conteneur ADN introuvable');
    return;
}
dnaContainer.appendChild(container);

complexityChart = new Chart(ctx, {
    type: 'line',
    data: {
        labels: positions,
        datasets: [{
            label: 'Complexité',
            data: complexityData,
            borderColor: '#00ffff',
            backgroundColor: 'rgba(0, 255, 255, 0.1)',
            borderWidth: 2,
            fill: true,
            tension: 0.4
        }]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                labels: { color: '#00ffff' }
            }
        },
        scales: {
            x: {
                title: { display: true, text: 'Position (bp)', color: '#00ffff' },
                ticks: { color: '#00ffff' },
                grid: { color: 'rgba(0, 255, 255, 0.1)' }
            },
            y: {
                title: { display: true, text: 'Complexité', color: '#00ffff' },
                ticks: { color: '#00ffff' },
                grid: { color: 'rgba(0, 255, 255, 0.1)' },
                min: 0,
                max: 1
            }
        }
    }
});
    } else {
complexityChart.data.labels = positions;
complexityChart.data.datasets[0].data = complexityData;
complexityChart.update();
    }
}

function calculateSequenceComplexity(sequence) {
    const length = sequence.length;
    if (length === 0) return 0;
    
    const counts = { A: 0, T: 0, C: 0, G: 0 };
    for (let nucleotide of sequence) {
if (counts.hasOwnProperty(nucleotide)) {
    counts[nucleotide]++;
}
    }
    
    let entropy = 0;
    for (let count of Object.values(counts)) {
if (count > 0) {
    const p = count / length;
    entropy -= p * Math.log2(p);
}
    }
    
    return entropy / 2; // Normaliser sur une échelle de 0 à 1
}

// ==========================================
// GESTIONNAIRE D'HISTORIQUE
// ==========================================

async function addToAnalysisHistory(analysisData, analysisId) {
    const historyItem = {
id: analysisId,
sequence_name: analysisData.sequence_name,
sequence_length: analysisData.original_sequence.length,
mutation_count: analysisData.mutations.length,
timestamp: new Date().toISOString(),
sequence_preview: analysisData.original_sequence.substring(0, 50)
    };

    // Ajouter côté frontend
    analysisHistory.unshift(historyItem);
    if (analysisHistory.length > 20) analysisHistory = analysisHistory.slice(0, 20);
    updateHistoryDisplay();

    // --- NOUVEAU : sauvegarde automatique côté backend ---
    try {
const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.SAVE_HISTORY}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        user_id: currentUser?.id || null,
        sequence_name: analysisData.sequence_name,
        original_sequence: analysisData.original_sequence,
        mutations: analysisData.mutations,
        analysis_id: analysisId
    })
});
const result = await response.json();
if (result.success) {
    console.log('Analyse sauvegardée automatiquement', result.id);
} else {
    console.warn('Impossible de sauvegarder l’analyse automatiquement');
}
    } catch (error) {
console.error('Erreur sauvegarde historique:', error);
    }
}


function updateHistoryDisplay() {
    // Ajouter section historique si elle n'existe pas
    if (!document.getElementById('analysisHistory')) {
createHistorySection();
    }
    
    const container = document.getElementById('historyList');
    if (!container) {
        // The active History tab uses #analysisHistory and its own renderer.
        return;
    }
    
    if (analysisHistory.length === 0) {
container.innerHTML = '<div class="text-gray-400 text-center py-4">Aucun historique</div>';
return;
    }
    
    const html = analysisHistory.map(item => `
<div class="p-3 bg-gray-800 rounded hover:bg-gray-700 transition-colors cursor-pointer"
     onclick="loadAnalysis('${item.id}')">
    <div class="flex justify-between items-start mb-2">
        <span class="font-bold text-cyan-400">${item.sequence_name}</span>
        <span class="text-xs text-gray-400">${formatDate(item.timestamp)}</span>
    </div>
    <div class="text-sm text-gray-300 mb-1">
        Longueur: ${item.sequence_length} bp | Mutations: ${item.mutation_count}
    </div>
    <div class="text-xs font-mono text-green-400">
        ${item.sequence_preview}...
    </div>
</div>
    `).join('');
    
    container.innerHTML = html;
}

function createHistorySection() {
    const historyHTML = `
<div class="hologram p-6 mt-6" id="analysisHistory">
    <h3 class="text-xl font-bold mb-4 text-cyan-400">
        <i class="fas fa-history mr-2"></i>
        Historique des Analyses
    </h3>
    <div id="historyList" class="space-y-2 max-h-80 overflow-y-auto">
    </div>
    <div class="mt-4 flex justify-center">
        <button onclick="loadAnalysisHistory()" class="btn-cyber">
            <i class="fas fa-sync mr-2"></i>
            Actualiser l'historique
        </button>
    </div>
</div>
    `;
    
    const host = document.getElementById('analysis');
    if (host) host.appendChild(createElementFromHTML(historyHTML));
}

async function loadAnalysisHistory() {
    try {
const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.GET_HISTORY}&user_id=${currentUser?.id || null}`);
const result = await response.json();

if (result.success) {
    analysisHistory = result.data.map(item => ({
        id: item.id,
        sequence_name: item.sequence_name || 'Analyse sans nom',
        sequence_length: item.sequence_length,
        mutation_count: item.mutation_count || 0,
        timestamp: item.created_at,
        sequence_preview: item.original_sequence?.substring(0, 50) || ''
    }));
    
    updateHistoryDisplay();
    showNotification('Historique mis à jour', 'success');
}
    } catch (error) {
console.error('Load history error:', error);
showNotification('Erreur chargement historique', 'error');
    }
}

