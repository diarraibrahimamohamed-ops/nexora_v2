function setupAdvancedFeatures() {
    // Configuration des raccourcis clavier
    document.addEventListener('keydown', function(e) {
if (e.ctrlKey || e.metaKey) {
    switch(e.key) {
        case 's':
            e.preventDefault();
            if (window.currentAnalysisId) {
                saveAnalysisToDatabase();
            }
            break;
        case 'r':
            e.preventDefault();
            generateRandomSequence();
            break;
        case 'i':
            e.preventDefault();
            if (!document.getElementById('aiInterpretButton').disabled) {
                requestAIInterpretation();
            }
            break;
    }
}
    });
    
    // Auto-save périodique
    setInterval(() => {
if (currentSequence && window.currentAnalysisId) {
    saveAnalysisToDatabase();
}
    }, 300000); // Toutes les 5 minutes
}

function setupEventListeners() {
    // Listener pour la recherche en temps réel
    document.getElementById('sequenceInput')?.addEventListener('input', debounce(() => {
if (document.getElementById('sequenceInput').value.length > 50) {
    analyzeSequence();
}
    }, 2000));
}

function debounce(func, delay) {
    let timeoutId;
    return function(...args) {
clearTimeout(timeoutId);
timeoutId = setTimeout(() => func.apply(this, args), delay);
    };
}

// Export des fonctions principales pour utilisation externe
window.uploadFastaFile = uploadFastaFile;
window.processFastaFile = processFastaFile;
window.BiotechPlatform = {
    analyzeSequence,
    uploadFastaFile,
    searchNCBI,
    requestAIInterpretation,
    saveAnalysisToDatabase,
    loadAnalysisHistory
};

// Export global pour accès direct depuis HTML
window.requestAIInterpretation = requestAIInterpretation;
window.saveAnalysisToDatabase = saveAnalysisToDatabase;
window.generateRandomSequence = generateRandomSequence;
window.saveRecentNCBISearch = saveRecentNCBISearch;

 function generateRandomSequence() {
    const nucleotides = ['A', 'T', 'C', 'G'];
    let sequence = 'ATG'; // Start with start codon
    
    for (let i = 3; i < 97; i++) {
        sequence += nucleotides[Math.floor(Math.random() * nucleotides.length)];
    }
    
    // Add stop codon
    sequence += 'TGA';
    
    document.getElementById('sequenceInput').value = sequence;
    
    // Generate reference with some mutations
    let reference = '';
    for (let i = 0; i < sequence.length; i++) {
        if (Math.random() < 0.03) { // 3% mutation rate
            const availableNucleotides = nucleotides.filter(n => n !== sequence[i]);
            reference += availableNucleotides[Math.floor(Math.random() * availableNucleotides.length)];
        } else {
            reference += sequence[i];
        }
    }
    
    document.getElementById('referenceInput').value = reference;
}

     // REMPLACER la fonction manquante par cette version complète :
function importSelectedSequences() {
    // Récupérer toutes les séquences sélectionnées
    const selectedSequences = document.querySelectorAll('.ncbi-result.selected');
    
    if (selectedSequences.length === 0) {
showNotification('Aucune séquence sélectionnée', 'warning');
return;
    }
    
    showNotification(`Import de ${selectedSequences.length} séquence(s) en cours...`, 'info');
    
    const importPromises = [];
    
    selectedSequences.forEach((element, index) => {
const accession = element.dataset.accession;
const database = element.dataset.database || 'nucleotide';

if (accession) {
    importPromises.push(importSingleSequence(accession, database, index));
}
    });
    
    Promise.all(importPromises).then(results => {
const successful = results.filter(r => r.success).length;
const failed = results.length - successful;

if (successful > 0) {
    // Prendre la première séquence réussie pour l'analyse
    const firstSuccess = results.find(r => r.success);
    if (firstSuccess) {
        document.getElementById('sequenceInput').value = firstSuccess.sequence;
        currentSequence = firstSuccess.sequence;
        analyzeSequence();
    }
}

let message = `Import terminé: ${successful} réussi(s)`;
if (failed > 0) message += `, ${failed} échec(s)`;

showNotification(message, successful > 0 ? 'success' : 'error');
updateCounters();

    }).catch(error => {
console.error('Import error:', error);
showNotification('Erreur lors de l\'import des séquences', 'error');
    });
}

async function importSingleSequence(accession, database, index) {
    try {
const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.FETCH_SEQUENCE}&accession=${accession}&database=${database}`);
const result = await response.json();

if (result.success && result.data) {
    // Parser la séquence FASTA
    const lines = result.data.sequence_data.split('\n');
    const sequence = lines.slice(1).join('').replace(/[^ATCGRYKMSWBDHVNU]/gi, '').toUpperCase();
    
    return {
        success: true,
        accession: accession,
        sequence: sequence,
        header: lines[0],
        length: sequence.length
    };
} else {
    throw new Error(result.error || 'Erreur inconnue');
}
    } catch (error) {
console.error(`Import failed for ${accession}:`, error);
return {
    success: false,
    accession: accession,
    error: error.message
};
    }
}

// 🔹 Statistiques NCBI
function updateNCBIStatsChart() {
    const recent = JSON.parse(localStorage.getItem('recentNCBISearches') || '[]');

    // Compter les occurrences par base de données
    const dbCount = {};
    recent.forEach(item => {
if (!dbCount[item.database]) dbCount[item.database] = 0;
dbCount[item.database]++;
    });

    const labels = Object.keys(dbCount);
    const data = Object.values(dbCount);

    // Supprimer l'ancien chart si déjà existant
    if (window.ncbiChart) {
window.ncbiChart.destroy();
    }

    const ctx = document.getElementById('ncbiStatsChart').getContext('2d');
    window.ncbiChart = new Chart(ctx, {
type: 'bar',
data: {
    labels: labels,
    datasets: [{
        label: 'Nombre de recherches',
        data: data,
        backgroundColor: 'rgba(14, 116, 144, 0.7)',
        borderColor: 'rgba(14, 116, 144, 1)',
        borderWidth: 1
    }]
},
options: {
    responsive: true,
    plugins: {
        legend: { display: false },
        title: {
            display: true,
            text: 'Statistiques des recherches NCBI récentes',
            color: '#00FFFF',
            font: { size: 16 }
        }
    },
    scales: {
        y: {
            beginAtZero: true,
            ticks: { color: '#fff' }
        },
        x: {
            ticks: { color: '#fff' }
        }
    }
}
    });
}
