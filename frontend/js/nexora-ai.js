function initializeAIAssistant() {
    const aiHTML = `
<div class="hologram p-6 mb-6">
    <h3 class="text-xl font-bold mb-4 text-cyan-400">
        <i class="fas fa-robot mr-2"></i>
        Assistant IA - Interprétation Scientifique
    </h3>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
            <div class="bg-black bg-opacity-30 rounded p-4 mb-4">
                <h4 class="font-bold text-green-400 mb-2">État de l'analyse</h4>
                <div id="aiAnalysisStatus" class="space-y-1 text-sm">
                    <div class="flex items-center">
                        <i class="fas fa-circle text-gray-400 mr-2"></i>
                        <span>En attente d'analyse...</span>
                    </div>
                </div>
            </div>
            <button onclick="requestAIInterpretation()" id="aiInterpretButton" class="btn-cyber w-full" disabled>
                <i class="fas fa-brain mr-2"></i>
                Demander Interprétation IA
            </button>
        </div>
        <div>
            <h4 class="font-bold text-purple-400 mb-2">Interprétations IA</h4>
            <div id="aiConclusions" class="space-y-3 max-h-80 overflow-y-auto">
                <div class="text-gray-400 text-center py-8">
                    <i class="fas fa-brain text-4xl mb-4 opacity-50"></i>
                    <p>Aucune interprétation disponible</p>
                </div>
            </div>
        </div>
    </div>
</div>
    `;

    // Insérer dans l'onglet Analyse ADN (utiliser analysis au lieu de dna)
    const analysisTab = document.getElementById('analysis');
    if (analysisTab) {
const container = document.createElement('div');
container.innerHTML = aiHTML;
// Utiliser appendChild au lieu d'insertBefore pour éviter les problèmes avec firstChild
analysisTab.appendChild(container);
    } else {
console.warn('[initializeAIAssistant] Container #analysis not found');
    }
}

async function requestAIInterpretation() {
    console.log('requestAIInterpretation appelée');
    console.log('currentAnalysisId:', window.currentAnalysisId);
    
    // Si pas d'analysis_id, sauvegarder d'abord
    if (!window.currentAnalysisId && currentSequence) {
console.log('Pas de currentAnalysisId, sauvegarde automatique...');
await saveAnalysisToDatabase();
console.log('Sauvegarde terminée, currentAnalysisId:', window.currentAnalysisId);
    }
    
    // Essayer de trouver le bouton qui a été cliqué
    let button = document.getElementById('interpreter') || document.getElementById('aiInterpretButton');
    let originalText = 'Interpreter';
    
    if (button) {
console.log('Bouton trouvé:', button.id);
originalText = button.innerHTML;
button.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>IA en cours...';
button.disabled = true;
    } else {
console.warn('Aucun bouton trouvé');
    }
    
    try {
const token = localStorage.getItem('token') || sessionStorage.getItem('token');

const response = await fetch(`${CONFIG.API.BASE_URL}${CONFIG.API.ENDPOINTS.INTERPRET_ANALYSIS}`, {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({
        analysis_id: window.currentAnalysisId || null
    })
});

console.log('Response status:', response.status);
const result = await response.json();
console.log('Response result:', result);

if (result.success) {
    displayAIInterpretations(result);
    
    // Mettre à jour le graphe avec les métriques
    if (result.metrics && typeof updateInterpretationChart === 'function') {
        updateInterpretationChart(result.metrics);
    }
    
    showNotification('Interprétation IA terminée', 'success');
} else {
    showNotification('Erreur IA: ' + (result.error || result.detail || 'Erreur inconnue'), 'error');
}
    } catch (error) {
console.error('AI interpretation error:', error);
showNotification('Erreur de connexion IA', 'error');
    } finally {
if (button) {
    button.innerHTML = originalText;
    button.disabled = false;
}
    }
}

function displayAIInterpretations(result) {
    const container = document.getElementById('aiconclusion');
    
    if (!container) {
console.error('Container aiconclusion not found');
return;
    }
    
    // Nouveau format API Python
    if (result.interpretation) {
const html = `
    <div class="bg-gray-800 rounded p-4 border-l-4 border-cyan-400">
        <h5 class="font-bold text-cyan-400 mb-2">
            <i class="fas fa-dna mr-2"></i>
            Analyse Génomique Avancée
        </h5>
        <div class="text-sm text-gray-300 mb-3 leading-relaxed whitespace-pre-wrap">
            ${result.interpretation}
        </div>
        <div class="flex justify-between items-center">
            <span class="text-xs text-green-400">
                <i class="fas fa-chart-line mr-1"></i>
                Confiance: ${result.confidence_score || 0}%
            </span>
            <span class="text-xs text-gray-400">
                <i class="fas fa-flask mr-1"></i>
                ${result.version || 'NEXORA PRO+'}
            </span>
        </div>
    </div>
`;
container.innerHTML = html;
    } 
    // Ancien format (compatibilité)
    else if (result.interpretations) {
const html = Object.entries(result.interpretations).map(([type, data]) => `
    <div class="bg-gray-800 rounded p-4 border-l-4 ${getTypeColor(type)}">
        <h5 class="font-bold text-cyan-400 mb-2">
            <i class="${getTypeIcon(type)} mr-2"></i>
            ${formatInterpretationType(type)}
        </h5>
        <div class="text-sm text-gray-300 mb-3 leading-relaxed">
            ${data.interpretation.substring(0, 300)}...
        </div>
        <div class="flex justify-between items-center">
            <span class="text-xs text-green-400">
                <i class="fas fa-chart-line mr-1"></i>
                Confiance: ${Math.round(data.confidence_score * 100)}%
            </span>
            <button onclick="showFullInterpretation('${type}', ${JSON.stringify(data).replace(/"/g, '&quot;')})" 
                    class="text-xs text-cyan-400 hover:text-cyan-300">
                <i class="fas fa-expand mr-1"></i>Voir complet
            </button>
        </div>
    </div>
`).join('');

container.innerHTML = html;
    } else {
container.innerHTML = '<div class="text-red-400">Erreur: Format de réponse non reconnu</div>';
    }
}

function getTypeColor(type) {
    const colors = {
'mutation_impact': 'border-red-400',
'resistance_profile': 'border-orange-400',
'protein_function': 'border-blue-400',
'clinical_significance': 'border-green-400'
    };
    return colors[type] || 'border-gray-400';
}

function getTypeIcon(type) {
    const icons = {
'mutation_impact': 'fas fa-exclamation-triangle',
'resistance_profile': 'fas fa-shield-alt',
'protein_function': 'fas fa-cube',
'clinical_significance': 'fas fa-user-md'
    };
    return icons[type] || 'fas fa-info-circle';
}

function formatInterpretationType(type) {
    const labels = {
'mutation_impact': 'Impact des Mutations',
'resistance_profile': 'Profil de Résistance',
'protein_function': 'Fonction Protéique',
'clinical_significance': 'Signification Clinique'
    };
    return labels[type] || type;
}

function showFullInterpretation(type, data) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    
    const parsedData = typeof data === 'string' ? JSON.parse(data.replace(/&quot;/g, '"')) : data;
    
    modal.innerHTML = `
<div class="modal-content" style="max-width: 900px;">
    <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
    <h2 class="text-xl font-bold mb-4 text-cyan-400">
        <i class="${getTypeIcon(type)} mr-2"></i>
        ${formatInterpretationType(type)} - Analyse Complète
    </h2>
    <div class="mb-4 p-4 bg-gray-800 rounded">
        <div class="flex justify-between items-center mb-2">
            <span class="font-semibold text-green-400">Niveau de confiance</span>
            <span class="text-cyan-400">${Math.round(parsedData.confidence_score * 100)}%</span>
        </div>
        <div class="w-full bg-gray-700 rounded-full h-2">
            <div class="bg-gradient-to-r from-green-400 to-cyan-400 h-2 rounded-full" 
                 style="width: ${parsedData.confidence_score * 100}%"></div>
        </div>
    </div>
    <div class="max-h-96 overflow-y-auto p-4 bg-black bg-opacity-30 rounded">
        <div class="text-gray-300 leading-relaxed whitespace-pre-wrap">${parsedData.interpretation}</div>
    </div>
    <div class="mt-4 flex justify-end space-x-4">
        <button onclick="exportInterpretation('${type}', ${JSON.stringify(parsedData).replace(/"/g, '&quot;')})" class="btn-cyber">
            <i class="fas fa-download mr-2"></i>Exporter
        </button>
        <button onclick="shareInterpretation('${type}', ${JSON.stringify(parsedData).replace(/"/g, '&quot;')})" class="btn-cyber">
            <i class="fas fa-share mr-2"></i>Partager
        </button>
    </div>
</div>
    `;
    
    document.body.appendChild(modal);
}



// ==========================================
// ANALYSE AVANCÉE ET SAUVEGARDE
// ==========================================

