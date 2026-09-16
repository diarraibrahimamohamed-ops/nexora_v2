async function searchNCBI() {
    const query = document.getElementById('ncbiQuery').value.trim();
    const database = document.getElementById('ncbiDatabase').value;
    const limit = document.getElementById('ncbiLimit')?.value || 20;
    const organism = document.getElementById('ncbiOrganism')?.value || '';

    if (!query) {
alert('Veuillez entrer un terme de recherche');
return;
    }

    const resultsContainer = document.getElementById('ncbiResults');
    resultsContainer.innerHTML = `
<div class="text-center text-cyan-400 py-8">
    <i class="fas fa-spinner fa-spin text-4xl mb-4"></i>
    <p>Recherche en cours...</p>
</div>
    `;

    try {
const url = `/api/v1/sequences/ncbi?&query=${encodeURIComponent(query)}&database=${encodeURIComponent(database)}&limit=${limit}&organism=${encodeURIComponent(organism)}`;
console.log('URL NCBI:', url);

const response = await fetch(url);
const result = await response.json();

console.log('Résultat NCBI:', result);

// ✅ Vérifications renforcées
if (!result.success || !Array.isArray(result.results) || result.results.length === 0) {
    resultsContainer.innerHTML = '<div class="text-yellow-400 text-center py-4">Aucun résultat trouvé</div>';
    return;
}

const validResults = result.results.filter(item => item.accession && item.title && item.organism);
if (validResults.length === 0) {
    resultsContainer.innerHTML = '<div class="text-yellow-400 text-center py-4">Aucun résultat valide trouvé</div>';
    return;
}

const html = validResults.map(item => `
    <div class="p-3 bg-gray-800 rounded hover:bg-gray-700 cursor-pointer transition-colors"
         onclick="fetchNCBISequence('${item.accession}', '${database}', '${item.title.replace(/'/g, "\\'")}', '${item.organism.replace(/'/g, "\\'")}', ${item.length || 0})">
        <div class="flex justify-between items-start mb-1">
            <span class="font-bold text-cyan-400">${item.accession}</span>
            <span class="text-xs text-green-400">${item.length || 0} bp</span>
        </div>
        <div class="text-sm text-white mb-1">${item.title}</div>
        <div class="text-xs text-gray-400">
            <span class="mr-4"><i class="fas fa-microscope mr-1"></i>${item.organism}</span>
            <span><i class="fas fa-calendar mr-1"></i>${item.update_date || 'N/A'}</span>
        </div>
    </div>
`).join('');

resultsContainer.innerHTML = html;
saveRecentNCBISearch(query, database);

    } catch (err) {
console.error('Erreur NCBI:', err);
resultsContainer.innerHTML = `<div class="text-red-400 text-center py-4">Erreur: ${err.message}</div>`;
    }
}




// 🔹 Récupération de la séquence et affichage modal
async function fetchNCBISequence(accession, database, title='', organism='', length=0) {
    try {
const url = `/api/v1/sequences/ncbi/fetch?&accession=${encodeURIComponent(accession)}&database=${encodeURIComponent(database)}`;
const response = await fetch(url);
const result = await response.json();

if (!result.success || !result.data) {
    alert('Impossible de récupérer la séquence NCBI');
    return;
}

const sequence = result.data.sequence_data.replace(/\s/g, '').toUpperCase();

// Affiche le modal avec toutes les infos
showNCBIMetadata({
    sequence_data: sequence,
    gene_name: result.data.gene_name || 'Non spécifié',
    organism: organism || 'Non spécifié',
    title: title
}, accession);
    } catch (err) {
console.error('Erreur récupération séquence:', err);
alert('Erreur lors de la récupération de la séquence');
    }
}

function showNCBIMetadata(data, accession) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';

    modal.innerHTML = `
<div class="modal-content">
    <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
    <h2 class="text-xl font-bold mb-4 text-cyan-400">
        <i class="fas fa-info-circle mr-2"></i>
        Métadonnées NCBI - ${accession}
    </h2>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
            <h3 class="font-bold text-green-400 mb-2">Informations générales</h3>
            <div class="space-y-2 text-sm">
                <div><strong>Accession:</strong> ${accession}</div>
                <div><strong>Organisme:</strong> ${data.organism}</div>
                <div><strong>Gène:</strong> ${data.gene_name}</div>
                <div><strong>Longueur:</strong> ${data.sequence_data.replace(/[^ATCG]/gi, '').length} bp</div>
            </div>
        </div>
        <div>
            <h3 class="font-bold text-purple-400 mb-2">Actions</h3>
            <div class="space-y-2">
                <button onclick="useAsReference('${data.sequence_data.replace(/[^ATCG]/gi, '')}')" class="btn-cyber w-full text-sm">
                    <i class="fas fa-plus mr-2"></i>Utiliser comme référence
                </button>
                <button onclick="compareWithNCBI('${data.sequence_data.replace(/[^ATCG]/gi, '')}')" class="btn-cyber w-full text-sm">
                    <i class="fas fa-balance-scale mr-2"></i>Comparer avec séquence courante
                </button>
                <button onclick="saveToProject('${accession}', '${data.sequence_data.replace(/[^ATCG]/gi, '')}')" class="btn-cyber w-full text-sm">
                    <i class="fas fa-save mr-2"></i>Sauvegarder dans projet
                </button>
                <button onclick="importNCBISequence('${data.sequence_data.replace(/[^ATCG]/gi, '')}')" class="btn-cyber w-full text-sm">
                    <i class="fas fa-download mr-2"></i>Importer Séquence
                </button>
            </div>
        </div>
    </div>
</div>
    `;

    document.body.appendChild(modal);
}

function useAsReference(sequence) {
    document.getElementById('referenceInput').value = sequence;
    document.querySelector('.modal').remove();
    analyzeSequence();
    showNotification('Séquence NCBI définie comme référence', 'success');
}

function compareWithNCBI(sequence) {
    const currentSeq = document.getElementById('sequenceInput').value;
    if (!currentSeq) {
showNotification('Aucune séquence courante à comparer', 'error');
return;
    }
    
    document.getElementById('referenceInput').value = sequence;
    document.querySelector('.modal').remove();
    analyzeSequence();
    showNotification('Comparaison avec séquence NCBI lancée', 'info');
}

function importNCBISequence(sequence) {
    // Importer la séquence dans le champ de saisie
    document.getElementById('sequenceInput').value = sequence;
    document.querySelector('.modal').remove();
    showNotification('Séquence NCBI importée', 'success');
    // Basculer vers l'onglet d'analyse
    if (typeof showTab === 'function') {
showTab('analysis');
    }
}

function saveToProject(accession, sequence) {
    console.log('saveToProject appelé avec accession:', accession);
    console.log('Longueur de la séquence:', sequence.length);
    
    // Sauvegarder la séquence au format FASTA dans le dossier storage via l'API
    const token = localStorage.getItem('token') || sessionStorage.getItem('token');
    
    fetch('/api/v1/sequences/save-fasta', {
method: 'POST',
headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`
},
body: JSON.stringify({
    accession: accession,
    sequence: sequence,
    header: accession
})
    })
    .then(response => {
console.log('Réponse reçue, status:', response.status);
return response.json();
    })
    .then(result => {
console.log('Résultat:', result);
if (result.success) {
    document.querySelector('.modal').remove();
    showNotification(result.message, 'success');
} else {
    showNotification('Erreur lors de la sauvegarde: ' + (result.detail || result.error), 'error');
}
    })
    .catch(error => {
console.error('Erreur:', error);
showNotification('Erreur de connexion au serveur', 'error');
    });
}

function saveRecentNCBISearch(query, database) {
    let recent = JSON.parse(localStorage.getItem('recentNCBISearches') || '[]');
    const search = { query, database, timestamp: Date.now() };
    
    // Éviter les doublons
    recent = recent.filter(item => item.query !== query || item.database !== database);
    recent.unshift(search);
    recent = recent.slice(0, 5); // Garder les 5 plus récents
    
    localStorage.setItem('recentNCBISearches', JSON.stringify(recent));
    loadRecentNCBISearches();
}

function loadRecentNCBISearches() {
    const recent = JSON.parse(localStorage.getItem('recentNCBISearches') || '[]');
    const container = document.getElementById('recentNCBISearches');
    
    // Si le conteneur n'existe pas, ne rien faire
    if (!container) {
return;
    }
    
    // Vérifier si on est dans l'onglet NCBI
    const ncbiTab = document.getElementById('ncbi');
    if (!ncbiTab || !ncbiTab.classList.contains('active')) {
// Si on n'est pas dans l'onglet NCBI, vider le conteneur
container.innerHTML = '<div class="text-gray-500 text-sm">Aucune recherche récente♣</div>';
return;
    }
    
    if (recent.length === 0) {
container.innerHTML = '<div class="text-gray-500">Aucune recherche</div>';
return;
    }
    
    const html = recent.map(search => `
<div class="cursor-pointer hover:text-cyan-400 transition-colors"
     onclick="document.getElementById('ncbiSearchInput').value='${search.query}'; document.getElementById('ncbiDatabase').value='${search.database}'; searchNCBI();">
    <i class="fas fa-search mr-1"></i>${search.query} (${search.database})
</div>
    `).join('');
    
    container.innerHTML = html;
}

// ♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣
// ASSISTANT IA AVANCÉ
// ♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣

