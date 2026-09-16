
// ♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣
// GESTION FASTA UPLOAD
// ♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣

function initializeFASTAUploader() {
    // The dedicated FASTA uploader already exists in the "Import FASTA" tab.
    // Never inject a second uploader into the DNA analysis tab.
    const dropZone = document.getElementById('dropArea');
    const fileInput = document.getElementById('fastaFile');
    if (!dropZone || !fileInput) {
        console.warn('[initializeFASTAUploader] Interface FASTA dédiée introuvable');
        return;
    }

    // Avoid duplicate listeners when initialization is called more than once.
    if (dropZone.dataset.nexoraFastaBound === '1') return;
    dropZone.dataset.nexoraFastaBound = '1';

    fileInput.addEventListener('change', handleFileSelect);
    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', handleDragOver);
    dropZone.addEventListener('dragleave', handleDragLeave);
    dropZone.addEventListener('drop', handleFileDrop);

    console.log('[initializeFASTAUploader] Uploader FASTA existant initialisé');
}

function handleDragOver(e) {
    e.preventDefault();
    e.currentTarget.classList.add('border-magenta-400', 'bg-cyan-400', 'bg-opacity-10');
}

function handleDragLeave(e) {
    e.preventDefault();
    e.currentTarget.classList.remove('border-magenta-400', 'bg-cyan-400', 'bg-opacity-10');
}

function handleFileDrop(e) {
    e.preventDefault();
    e.currentTarget.classList.remove('border-magenta-400', 'bg-cyan-400', 'bg-opacity-10');
    
    const files = e.dataTransfer.files;
    if (files.length > 0) {
processFastaFile(files[0]);
    }
}

function handleFileSelect(e) {
    const file = e.target.files[0];
    if (file) {
processFastaFile(file);
    }
}

// Fonction uploadFastaFile appelée par le bouton HTML
function uploadFastaFile() {
    const fileInput = document.getElementById('fastaFile');
    if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
showNotification('Veuillez d\'abord sélectionner un fichier FASTA', 'error');
return;
    }
    processFastaFile(fileInput.files[0]);
}

function processFastaFile(file) {
    // Si aucun fichier n'est fourni, prendre celui de l'input
    if (!file) {
const fileInput = document.getElementById('fastaFile');
if (!fileInput.files || fileInput.files.length === 0) {
    showNotification('Veuillez d\'abord sélectionner un fichier FASTA', 'error');
    return;
}
file = fileInput.files[0];
    }
    
    // Validation du fichier
    if (!validateFastaFile(file)) {
return;
    }
    
    // Afficher les informations du fichier
    updateFileInfo(file);
    
    // Lire et traiter le fichier
    const reader = new FileReader();
    reader.onload = function(e) {
try {
    const content = e.target.result;
    const sequences = parseFastaContent(content);
    
    if (sequences.length > 0) {
        fastaSequences = sequences;
        displayFastaPreview(sequences);
        document.getElementById('processBtn').disabled = false;
        showNotification(`${sequences.length} séquence(s) chargée(s) avec succès`, 'success');
    } else {
        showNotification('Aucune séquence valide trouvée dans le fichier', 'error');
        clearFastaPreview();
    }
} catch (error) {
    console.error('Erreur traitement FASTA:', error);
    showNotification('Erreur lors du traitement du fichier', 'error');
    clearFastaPreview();
}
    };
    
    reader.onerror = function() {
showNotification('Erreur lors de la lecture du fichier', 'error');
    };
    
    reader.readAsText(file);
}

// AJOUTER ces fonctions de validation et utilitaires :
function validateFastaFile(file) {
    const allowedExtensions = ['fasta', 'fa', 'fas', 'fna', 'txt'];
    const extension = file.name.split('.').pop().toLowerCase();
    
    if (!allowedExtensions.includes(extension)) {
showNotification(`Extension non supportée. Utilisez: ${allowedExtensions.join(', ')}`, 'error');
return false;
    }
    
    if (file.size > 10 * 1024 * 1024) { // Aligné sur la limite backend
        showNotification('Fichier trop volumineux (max: 10MB)', 'error');
        return false;
    }
    
    if (file.size === 0) {
showNotification('Le fichier est vide', 'error');
return false;
    }
    
    return true;
}

function updateFileInfo(file) {
    // Mettre à jour les informations du fichier dans l'interface
    const fileName = document.getElementById('fastaFileName');
    const fileSize = document.getElementById('fastaFileSize');
    
    if (fileName) fileName.textContent = file.name;
    if (fileSize) fileSize.textContent = formatFileSize(file.size);
}

function clearFastaPreview() {
    const preview = document.getElementById('fastaPreview');
    if (preview) {
preview.innerHTML = `
    <div class="text-center text-gray-400 mt-20">
        <i class="fas fa-file-alt text-4xl mb-4"></i>
        <p>Erreur lors du traitement du fichier</p>
    </div>
`;
    }
    document.getElementById('processBtn').disabled = false;
}

function parseFastaContent(content) {
    console.log('Parsing FASTA content, length:', content.length);
    
    if (!content || content.trim().length === 0) {
throw new Error('Contenu du fichier vide');
    }
    
    const sequences = [];
    const lines = content.split(/\r?\n/); // Support Windows et Unix line endings
    let currentSequence = null;
    let lineNumber = 0;
    
    for (let line of lines) {
lineNumber++;
line = line.trim();

// Ignorer les lignes vides
if (line.length === 0) continue;

if (line.startsWith('>')) {
    // Sauvegarder la séquence précédente si elle existe
    if (currentSequence && currentSequence.sequence.length > 0) {
        sequences.push(currentSequence);
    }
    
    // Nouvelle séquence
    currentSequence = {
        header: line.substring(1), // Enlever le '>'
        sequence: '',
        length: 0,
        lineStart: lineNumber
    };
    
    console.log('Nouvelle séquence trouvée:', currentSequence.header);
    
} else if (currentSequence) {
    // Nettoyer la ligne de séquence
    const cleanLine = line.toUpperCase().replace(/[^ATCGRYKMSWBDHVNU]/g, '');
    
    if (cleanLine.length !== line.length) {
        console.warn(`Ligne ${lineNumber}: caractères non-standard supprimés`);
    }
    
    currentSequence.sequence += cleanLine;
    currentSequence.length = currentSequence.sequence.length;
    
} else if (line.startsWith('>') === false && sequences.length === 0) {
    // Première ligne n'est pas un header FASTA
    throw new Error('Format FASTA invalide: le fichier doit commencer par une ligne d\'en-tête (>)');
}
    }
    
    // Ajouter la dernière séquence
    if (currentSequence && currentSequence.sequence.length > 0) {
sequences.push(currentSequence);
    }
    
    console.log(`Parsing terminé: ${sequences.length} séquence(s) trouvée(s)`);
    
    if (sequences.length === 0) {
throw new Error('Aucune séquence valide trouvée dans le fichier FASTA');
    }
    
    return sequences;
}

// AMÉLIORER votre fonction displayFastaPreview :
function displayFastaPreview(sequences) {
    const preview = document.getElementById('fastaPreview');
    if (!preview) {
console.error('Element fastaPreview not found');
return;
    }
    
    console.log('Displaying preview for', sequences.length, 'sequences');
    
    let html = '<div class="space-y-4">';
    
    // Afficher jusqu'à 3 séquences dans l'aperçu
    const previewCount = Math.min(sequences.length, 3);
    
    for (let i = 0; i < previewCount; i++) {
const seq = sequences[i];
const shortSequence = seq.sequence.length > 100 ? 
    seq.sequence.substring(0, 100) + '...' : seq.sequence;

html += `
    <div class="border border-cyan-400 rounded-lg p-4 bg-black bg-opacity-30">
        <div class="flex justify-between items-start mb-2">
            <h4 class="text-cyan-400 font-bold text-sm">Séquence ${i + 1}</h4>
            <button onclick="selectFastaSequence(${i}, '${seq.sequence.replace(/'/g, "\\'")}', '${seq.header.replace(/'/g, "\\'")}'); event.stopPropagation();" 
                    class="btn-cyber text-xs">
                <i class="fas fa-play mr-1"></i>Utiliser
            </button>
        </div>
        <div class="text-green-400 text-xs mb-2 break-all">
            &gt;${seq.header.length > 80 ? seq.header.substring(0, 80) + '...' : seq.header}
        </div>
        <div class="font-mono text-xs text-white mb-2 break-all">
            ${formatSequence(shortSequence, 'dna')}
        </div>
        <div class="flex justify-between text-xs text-gray-400">
            <span>Longueur: ${seq.length} bp</span>
            <span>Type: ${detectSequenceType(seq.sequence)}</span>
        </div>
    </div>
`;
    }
    
    if (sequences.length > 3) {
html += `
    <div class="text-center text-gray-400 text-sm p-4">
        <i class="fas fa-ellipsis-h mr-2"></i>
        ... et ${sequences.length - 3} autre(s) séquence(s)
        <button onclick="showAllSequences()" class="btn-cyber text-xs ml-4">
            <i class="fas fa-list mr-1"></i>Voir toutes
        </button>
    </div>
`;
    }
    
    html += '</div>';
    preview.innerHTML = html;
    
    // Mettre à jour le compteur
    const countElement = document.getElementById('fastaSequenceCount');
    if (countElement) {
countElement.textContent = sequences.length;
    }
}

// REMPLACER votre fonction processFastaFile par cette version robuste :
function processFastaFile(file) {
    // Si aucun fichier n'est fourni, prendre celui de l'input
    if (!file) {
const fileInput = document.getElementById('fastaFile');
if (!fileInput.files || fileInput.files.length === 0) {
    showNotification('Veuillez d\'abord sélectionner un fichier FASTA', 'error');
    return;
}
file = fileInput.files[0];
    }
    
    console.log('Processing FASTA file:', file.name, 'Size:', file.size);
    
    // Validation du fichier
    if (!validateFastaFile(file)) {
return;
    }
    
    // Afficher les informations du fichier
    updateFileInfo(file);
    
    // Afficher un indicateur de chargement
    const preview = document.getElementById('fastaPreview');
    if (preview) {
preview.innerHTML = `
    <div class="text-center text-cyan-400 py-8">
        <i class="fas fa-spinner fa-spin text-4xl mb-4"></i>
        <p>Traitement du fichier FASTA en cours...</p>
        <p class="text-sm text-gray-400">${file.name}</p>
    </div>
`;
    }
    
    // Lire et traiter le fichier avec gestion d'erreur robuste
    const reader = new FileReader();
    
    reader.onload = function(e) {
try {
    console.log('File read successfully, content length:', e.target.result.length);
    
    const content = e.target.result;
    
    // Vérification de base du contenu
    if (!content || content.trim().length === 0) {
        throw new Error('Le fichier est vide');
    }
    
    if (!content.includes('>')) {
        throw new Error('Format FASTA invalide: aucun header trouvé (pas de ligne commençant par >)');
    }
    
    const sequences = parseFastaContentRobust(content);
    
    if (sequences.length > 0) {
        fastaSequences = sequences;
        displayFastaPreviewEnhanced(sequences);
        document.getElementById('processBtn').disabled = false;
        showNotification(`${sequences.length} séquence(s) chargée(s) avec succès`, 'success');
        
        // Log pour debug
        console.log('Sequences parsed successfully:', sequences);
        
    } else {
        throw new Error('Aucune séquence valide trouvée dans le fichier');
    }
    
} catch (error) {
    console.error('Erreur traitement FASTA:', error);
    showNotification('Erreur: ' + error.message, 'error');
    clearFastaPreview();
    
    // Afficher des détails de debug dans la preview
    if (preview) {
        preview.innerHTML = `
            <div class="text-center text-red-400 py-8">
                <i class="fas fa-exclamation-triangle text-4xl mb-4"></i>
                <p class="font-bold">Erreur de traitement</p>
                <p class="text-sm">${error.message}</p>
                <button onclick="showFileDebugInfo()" class="btn-cyber text-xs mt-4">
                    <i class="fas fa-info-circle mr-1"></i>Voir détails
                </button>
            </div>
        `;
    }
}
    };
    
    reader.onerror = function(error) {
console.error('File reader error:', error);
showNotification('Erreur lors de la lecture du fichier', 'error');
clearFastaPreview();
    };
    
    // Lire le fichier comme texte avec encodage UTF-8
    reader.readAsText(file, 'UTF-8');
}

// Fonction de parsing FASTA plus robuste
function parseFastaContentRobust(content) {
    console.log('Parsing FASTA content...');
    
    const sequences = [];
    const lines = content.split(/\r?\n/);
    let currentSequence = null;
    let lineNumber = 0;
    let totalLines = lines.length;
    
    console.log(`Total lines to process: ${totalLines}`);
    
    for (let line of lines) {
lineNumber++;
const originalLine = line;
line = line.trim();

// Ignorer les lignes vides et commentaires
if (line.length === 0 || line.startsWith(';')) {
    continue;
}

if (line.startsWith('>')) {
    // Sauvegarder la séquence précédente
    if (currentSequence && currentSequence.sequence.length > 0) {
        currentSequence.length = currentSequence.sequence.length;
        sequences.push(currentSequence);
        console.log(`Séquence sauvée: ${currentSequence.header.substring(0, 50)}... (${currentSequence.length} bp)`);
    }
    
    // Nouvelle séquence
    currentSequence = {
        id: sequences.length + 1,
        header: line.substring(1).trim(), // Enlever le '>' et espaces
        sequence: '',
        length: 0,
        lineStart: lineNumber,
        type: 'DNA' // Par défaut
    };
    
    console.log(`Nouvelle séquence ligne ${lineNumber}: ${currentSequence.header.substring(0, 50)}...`);
    
} else if (currentSequence) {
    // Ligne de séquence
    let cleanLine = line.toUpperCase();
    
    // Enlever les caractères non-valides mais garder les ambigus
    const validChars = /[ATCGRYKMSWBDHVNU]/g;
    const validSequence = cleanLine.match(validChars);
    
    if (validSequence) {
        const cleanSequence = validSequence.join('');
        currentSequence.sequence += cleanSequence;
        
        // Détecter le type de séquence
        if (cleanSequence.includes('U')) {
            currentSequence.type = 'RNA';
        } else if (/[FILVMPWYA]/.test(cleanSequence)) {
            currentSequence.type = 'Protein';
        }
        
        // Avertir si des caractères ont été supprimés
        if (cleanSequence.length !== cleanLine.length) {
            const removed = cleanLine.replace(validChars, '').replace(/\s+/g, '');
            if (removed.length > 0) {
                console.warn(`Ligne ${lineNumber}: caractères supprimés: "${removed}"`);
            }
        }
    }
    
} else {
    // Ligne de séquence sans header
    console.warn(`Ligne ${lineNumber}: séquence trouvée avant un header FASTA`);
}

// Progress indication pour gros fichiers
if (lineNumber % 1000 === 0) {
    console.log(`Progression: ${lineNumber}/${totalLines} lignes (${Math.round(lineNumber/totalLines*100)}%)`);
}
    }
    
    // Ajouter la dernière séquence
    if (currentSequence && currentSequence.sequence.length > 0) {
currentSequence.length = currentSequence.sequence.length;
sequences.push(currentSequence);
console.log(`Dernière séquence sauvée: ${currentSequence.header.substring(0, 50)}... (${currentSequence.length} bp)`);
    }
    
    console.log(`Parsing terminé: ${sequences.length} séquence(s) trouvée(s)`);
    
    // Validation finale
    const validSequences = sequences.filter(seq => seq.sequence.length >= 10);
    if (validSequences.length < sequences.length) {
console.warn(`${sequences.length - validSequences.length} séquence(s) ignorée(s) (trop courtes < 10 bp)`);
    }
    
    return validSequences;
}

// Fonction d'affichage améliorée
function displayFastaPreviewEnhanced(sequences) {
    const preview = document.getElementById('fastaPreview');
    if (!preview) return;
    
    console.log('Displaying enhanced preview for', sequences.length, 'sequences');
    
    let html = `
<div class="mb-4 p-3 bg-gray-800 rounded-lg">
    <div class="flex justify-between items-center">
        <span class="font-bold text-cyan-400">
            <i class="fas fa-dna mr-2"></i>${sequences.length} séquence(s) chargée(s)
        </span>
        <div class="space-x-2">
            <button onclick="analyzeAllSequences()" class="btn-cyber text-xs">
                <i class="fas fa-play mr-1"></i>Analyser toutes
            </button>
            <button onclick="exportSequences()" class="btn-cyber text-xs">
                <i class="fas fa-download mr-1"></i>Exporter
            </button>
        </div>
    </div>
</div>
<div class="space-y-3 max-h-80 overflow-y-auto">
    `;
    
    // Statistiques rapides
    const stats = calculateSequenceStats(sequences);
    html += `
<div class="grid grid-cols-2 md:grid-cols-4 gap-4 p-3 bg-black bg-opacity-30 rounded">
    <div class="text-center">
        <div class="text-cyan-400 font-bold">${stats.totalLength}</div>
        <div class="text-xs text-gray-400">Total bp</div>
    </div>
    <div class="text-center">
        <div class="text-green-400 font-bold">${stats.avgLength}</div>
        <div class="text-xs text-gray-400">Moy. bp</div>
    </div>
    <div class="text-center">
        <div class="text-purple-400 font-bold">${stats.maxLength}</div>
        <div class="text-xs text-gray-400">Max bp</div>
    </div>
    <div class="text-center">
        <div class="text-orange-400 font-bold">${stats.avgGC}%</div>
        <div class="text-xs text-gray-400">Moy. GC</div>
    </div>
</div>
    `;
    
    // Afficher chaque séquence
    sequences.forEach((seq, index) => {
const shortSequence = seq.sequence.length > 80 ? 
    seq.sequence.substring(0, 80) + '...' : seq.sequence;

const gcContent = calculateGCContent(seq.sequence);

html += `
    <div class="border border-gray-600 rounded-lg p-4 hover:border-cyan-400 transition-colors bg-black bg-opacity-20">
        <div class="flex justify-between items-start mb-2">
            <div class="flex-1">
                <h4 class="text-cyan-400 font-bold text-sm flex items-center">
                    <span class="bg-cyan-400 text-black px-2 py-1 rounded-full text-xs mr-2">${index + 1}</span>
                    Séquence ${seq.type}
                </h4>
                <p class="text-green-400 text-xs mt-1 break-all">
                    >${seq.header.length > 60 ? seq.header.substring(0, 60) + '...' : seq.header}
                </p>
            </div>
            <div class="flex space-x-1">
                <button onclick="selectAndAnalyzeSequence(${index})" 
                        class="btn-cyber text-xs" title="Analyser cette séquence">
                    <i class="fas fa-play"></i>
                </button>
                <button onclick="copySequenceToClipboard(${index})" 
                        class="btn-cyber text-xs" title="Copier la séquence">
                    <i class="fas fa-copy"></i>
                </button>
                <button onclick="showSequenceDetails(${index})" 
                        class="btn-cyber text-xs" title="Voir les détails">
                    <i class="fas fa-info"></i>
                </button>
            </div>
        </div>
        
        <div class="font-mono text-xs text-white mb-3 bg-gray-900 p-2 rounded border-l-2 border-cyan-400">
            ${formatSequencePreview(shortSequence)}
        </div>
        
        <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
            <div class="text-center bg-gray-800 rounded p-2">
                <div class="text-cyan-400 font-bold">${seq.length}</div>
                <div class="text-gray-400">bp</div>
            </div>
            <div class="text-center bg-gray-800 rounded p-2">
                <div class="text-green-400 font-bold">${gcContent.toFixed(1)}%</div>
                <div class="text-gray-400">GC</div>
            </div>
            <div class="text-center bg-gray-800 rounded p-2">
                <div class="text-purple-400 font-bold">${seq.type}</div>
                <div class="text-gray-400">Type</div>
            </div>
            <div class="text-center bg-gray-800 rounded p-2">
                <div class="text-orange-400 font-bold" id="analysis-status-${index}">
                    <i class="fas fa-clock"></i>
                </div>
                <div class="text-gray-400">Statut</div>
            </div>
        </div>
    </div>
`;
    });
    
    html += '</div>';
    preview.innerHTML = html;
    
    // Mettre à jour le compteur
    const countElement = document.getElementById('fastaSequenceCount');
    if (countElement) {
countElement.textContent = sequences.length;
    }
}

// AJOUTER cette fonction manquante :
function selectFastaSequence(index, sequence, header) {
    if (index >= 0 && index < fastaSequences.length) {
const seq = fastaSequences[index];

// Mettre la séquence dans l'analyseur
document.getElementById('sequenceInput').value = sequence || seq.sequence;
currentSequence = sequence || seq.sequence;

// Analyser automatiquement
showNotification(`Séquence "${header || seq.header}" sélectionnée pour analyse`, 'success');
analyzeSequence();

// Optionnel: changer d'onglet vers l'analyse
showTab('analysis');
    }
}

function showAllSequences() {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    
    let html = `
<div class="modal-content" style="max-width: 900px; max-height: 80vh;">
    <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
    <h2 class="text-xl font-bold mb-4 text-cyan-400">
        <i class="fas fa-list mr-2"></i>
        Toutes les séquences FASTA (${fastaSequences.length})
    </h2>
    <div class="max-h-96 overflow-y-auto space-y-3">
    `;
    
    fastaSequences.forEach((seq, index) => {
html += `
    <div class="border border-gray-600 rounded p-3 hover:border-cyan-400 transition-colors cursor-pointer"
         onclick="selectFastaSequence(${index}, '${seq.sequence.replace(/'/g, "\\'")}', '${seq.header.replace(/'/g, "\\'")}'); this.closest('.modal').remove();">
        <div class="flex justify-between items-start mb-2">
            <span class="font-bold text-cyan-400">Séquence ${index + 1}</span>
            <span class="text-xs text-gray-400">${seq.length} bp</span>
        </div>
        <div class="text-xs text-green-400 mb-2 break-all">
            ${seq.header.substring(0, 100)}${seq.header.length > 100 ? '...' : ''}
        </div>
        <div class="font-mono text-xs text-gray-300">
            ${seq.sequence.substring(0, 80)}${seq.sequence.length > 80 ? '...' : ''}
        </div>
    </div>
`;
    });
    
    html += `
    </div>
    <div class="mt-4 text-center">
        <button onclick="this.parentElement.parentElement.parentElement.remove()" class="btn-cyber">
            <i class="fas fa-times mr-2"></i>Fermer
        </button>
    </div>
</div>
    `;
    
    modal.innerHTML = html;
    document.body.appendChild(modal);
}

function showFastaSequenceSelector(sequences) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    
    modal.innerHTML = `
<div class="modal-content">
    <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
    <h2 class="text-xl font-bold mb-4 text-cyan-400">
        <i class="fas fa-list mr-2"></i>
        Sélectionner une séquence à analyser
    </h2>
    <div class="max-h-96 overflow-y-auto">
        ${sequences.map((seq, index) => `
            <div class="p-3 mb-2 bg-gray-800 rounded cursor-pointer hover:bg-gray-700 transition-colors"
                 onclick="selectFastaSequence(${index}, '${seq.sequence.replace(/'/g, "\\'")}')">
                <div class="font-bold text-cyan-400">${index + 1}. ${seq.header.substring(0, 80)}</div>
                <div class="text-sm text-gray-400">Longueur: ${seq.length} bp</div>
                <div class="text-xs font-mono text-green-400 mt-1">
                    ${seq.sequence.substring(0, 100)}${seq.sequence.length > 100 ? '...' : ''}
                </div>
            </div>
        `).join('')}
    </div>
</div>
    `;
    
    document.body.appendChild(modal);
}

function selectFastaSequence(index, sequence) {
    document.getElementById('sequenceInput').value = sequence;
    document.querySelector('.modal').remove();
    analyzeSequence();
    showNotification(`Analyse de la séquence #${index + 1}`, 'info');
}

// Fonction pour analyser toutes les séquences une par une
async function analyzeAllSequences() {
    if (!fastaSequences || fastaSequences.length === 0) {
showNotification('Aucune séquence à analyser', 'error');
return;
    }
    
    const total = fastaSequences.length;
    showNotification(`🚀 Début de l'analyse de ${total} séquence(s)...`, 'info');
    
    // Créer le modal de progression
    const progressModal = createAnalysisProgressModal(total);
    document.body.appendChild(progressModal);
    
    const results = [];
    let successful = 0;
    let failed = 0;
    
    for (let i = 0; i < fastaSequences.length; i++) {
const sequence = fastaSequences[i];

try {
    updateProgressModal(i + 1, total, `Analyse de: ${sequence.header.substring(0, 50)}...`);
    updateSequenceStatus(i, 'analyzing');
    
    // Analyser la séquence
    const analysisResult = await analyzeSingleSequence(sequence, i);
    
    if (analysisResult.success) {
        results.push(analysisResult);
        successful++;
        updateSequenceStatus(i, 'success');
    } else {
        failed++;
        updateSequenceStatus(i, 'error');
    }
    
    // Pause pour éviter de surcharger le système
    await new Promise(resolve => setTimeout(resolve, 1000));
    
} catch (error) {
    console.error(`Erreur analyse séquence ${i}:`, error);
    failed++;
    updateSequenceStatus(i, 'error');
}
    }
    
    // Fermer le modal de progression
    setTimeout(() => {
progressModal.remove();

// Afficher les résultats
showAnalysisResults(results, successful, failed);
showNotification(`Analyse terminée: ${successful} réussies, ${failed} échecs`, 'success');

    }, 1000);
}

async function analyzeSingleSequence(sequenceObj, index) {
    return new Promise(async (resolve) => {
try {
    const sequence = sequenceObj.sequence;
    const reference = sequence; // Utiliser la séquence comme référence par défaut
    
    // Analyses de base
    const basicAnalysis = {
        id: index,
        sequence_name: `${sequenceObj.header} (Auto-${index + 1})`,
        original_sequence: sequence,
        sequence_length: sequence.length,
        sequence_type: sequenceObj.type,
        gc_content: calculateGCContent(sequence),
        timestamp: new Date().toISOString()
    };
    
    // Analyse des nucléotides
    const nucleotideData = analyzeNucleotides(sequence);
    
    // Détection de mutations (comparaison avec une référence générique)
    const mutations = detectMutationsInSequence(sequence, reference);
    
    // Transcription et traduction
    const transcriptionData = transcribeAndTranslateSequence(sequence);
    
    // Analyse de complexité
    const complexity = calculateSequenceComplexity(sequence);
    
    // Prédiction de résistance (simulation)
    const resistanceData = predictResistanceProfile(sequence);
    
    const result = {
        success: true,
        sequence_index: index,
        basic_analysis: basicAnalysis,
        nucleotide_composition: nucleotideData,
        mutations: mutations,
        transcription: transcriptionData,
        complexity_score: complexity,
        resistance_prediction: resistanceData,
        analysis_time: new Date().toISOString()
    };
    
    // Sauvegarder dans l'historique
    addToAnalysisHistory(result, `analysis_${index}_${Date.now()}`);
    
    resolve(result);
    
} catch (error) {
    resolve({
        success: false,
        sequence_index: index,
        error: error.message
    });
}
    });
}

// Fonctions utilitaires pour l'analyse individuelle
function calculateSequenceStats(sequences) {
    const lengths = sequences.map(s => s.sequence.length);
    const gcContents = sequences.map(s => calculateGCContent(s.sequence));
    
    return {
totalLength: lengths.reduce((a, b) => a + b, 0),
avgLength: Math.round(lengths.reduce((a, b) => a + b, 0) / lengths.length),
maxLength: Math.max(...lengths),
minLength: Math.min(...lengths),
avgGC: Math.round(gcContents.reduce((a, b) => a + b, 0) / gcContents.length * 10) / 10
    };
}

function calculateGCContent(sequence) {
    const gcCount = (sequence.match(/[GC]/g) || []).length;
    return (gcCount / sequence.length) * 100;
}

function formatSequencePreview(sequence) {
    return sequence.replace(/(.{10})/g, '$1 ').trim();
}

function updateSequenceStatus(index, status) {
    const statusElement = document.getElementById(`analysis-status-${index}`);
    if (statusElement) {
const icons = {
    'analyzing': '<i class="fas fa-spinner fa-spin text-yellow-400"></i>',
    'success': '<i class="fas fa-check text-green-400"></i>',
    'error': '<i class="fas fa-times text-red-400"></i>',
    'pending': '<i class="fas fa-clock text-gray-400"></i>'
};
statusElement.innerHTML = icons[status] || icons.pending;
    }
}

function createAnalysisProgressModal(total) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    modal.id = 'batchAnalysisModal';
    
    modal.innerHTML = `
<div class="modal-content" style="max-width: 600px;">
    <h2 class="text-xl font-bold mb-4 text-cyan-400">
        <i class="fas fa-cogs mr-2"></i>
        Analyse en lot - ${total} séquences
    </h2>
    <div class="mb-4">
        <div class="w-full bg-gray-700 rounded-full h-4">
            <div id="batchProgress" class="bg-gradient-to-r from-cyan-400 to-purple-400 h-4 rounded-full transition-all duration-500" 
                 style="width: 0%"></div>
        </div>
        <div class="flex justify-between mt-2 text-sm">
            <span id="progressText">Démarrage...</span>
            <span id="progressCount">0 / ${total}</span>
        </div>
    </div>
    <div id="currentSequenceInfo" class="text-sm text-gray-400 bg-gray-800 p-3 rounded">
        Préparation de l'analyse...
    </div>
</div>
    `;
    
    return modal;
}

function updateProgressModal(current, total, message) {
    const progress = document.getElementById('batchProgress');
    const progressText = document.getElementById('progressText');
    const progressCount = document.getElementById('progressCount');
    const currentInfo = document.getElementById('currentSequenceInfo');
    
    if (progress) {
const percentage = (current / total) * 100;
progress.style.width = percentage + '%';
    }
    
    if (progressText) progressText.textContent = `Séquence ${current} sur ${total}`;
    if (progressCount) progressCount.textContent = `${current} / ${total}`;
    if (currentInfo) currentInfo.textContent = message;
}

function showAnalysisResults(results, successful, failed) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    
    let html = `
<div class="modal-content" style="max-width: 1000px; max-height: 80vh;">
    <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
    <h2 class="text-xl font-bold mb-4 text-cyan-400">
        <i class="fas fa-chart-bar mr-2"></i>
        Résultats de l'analyse en lot
    </h2>
    
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div class="bg-green-600 bg-opacity-20 border border-green-400 rounded p-4 text-center">
            <div class="text-2xl font-bold text-green-400">${successful}</div>
            <div class="text-sm">Réussies</div>
        </div>
        <div class="bg-red-600 bg-opacity-20 border border-red-400 rounded p-4 text-center">
            <div class="text-2xl font-bold text-red-400">${failed}</div>
            <div class="text-sm">Échecs</div>
        </div>
        <div class="bg-blue-600 bg-opacity-20 border border-blue-400 rounded p-4 text-center">
            <div class="text-2xl font-bold text-blue-400">${results.length}</div>
            <div class="text-sm">Total</div>
        </div>
    </div>
    
    <div class="max-h-96 overflow-y-auto">
    `;
    
    results.forEach((result, index) => {
if (result.success) {
    html += `
        <div class="border border-gray-600 rounded p-4 mb-3 hover:border-cyan-400 transition-colors">
            <div class="flex justify-between items-start mb-2">
                <h4 class="font-bold text-cyan-400">Séquence ${result.sequence_index + 1}</h4>
                <button onclick="viewDetailedResults(${index})" class="btn-cyber text-xs">
                    <i class="fas fa-eye mr-1"></i>Détails
                </button>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
                <div>Longueur: <span class="text-cyan-400">${result.basic_analysis.sequence_length} bp</span></div>
                <div>GC: <span class="text-green-400">${result.basic_analysis.gc_content.toFixed(1)}%</span></div>
                <div>Mutations: <span class="text-yellow-400">${result.mutations.length}</span></div>
                <div>Complexité: <span class="text-purple-400">${(result.complexity_score * 100).toFixed(1)}%</span></div>
            </div>
        </div>
    `;
}
    });
    
    html += `
    </div>
    <div class="mt-4 flex justify-end space-x-4">
        <button onclick="exportBatchResults()" class="btn-cyber">
            <i class="fas fa-download mr-2"></i>Exporter résultats
        </button>
        <button onclick="this.parentElement.parentElement.parentElement.remove()" class="btn-cyber">
            <i class="fas fa-times mr-2"></i>Fermer
        </button>
    </div>
</div>
    `;
    
    modal.innerHTML = html;
    document.body.appendChild(modal);
    
}

// Fonctions additionnelles
function selectAndAnalyzeSequence(index) {
    if (fastaSequences[index]) {
const seq = fastaSequences[index];
document.getElementById('sequenceInput').value = seq.sequence;
currentSequence = seq.sequence;

showNotification(`Analyse de: ${seq.header.substring(0, 50)}...`, 'info');
showTab('analysis');
analyzeSequence();
    }
}

function copySequenceToClipboard(index) {
    if (fastaSequences[index]) {
const seq = fastaSequences[index];
navigator.clipboard.writeText(seq.sequence).then(() => {
    showNotification('Séquence copiée dans le presse-papier', 'success');
});
    }
}

function showSequenceDetails(index) {
    if (fastaSequences[index]) {
const seq = fastaSequences[index];
const modal = document.createElement('div');
modal.className = 'modal';
modal.style.display = 'block';

modal.innerHTML = `
    <div class="modal-content">
        <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
        <h2 class="text-xl font-bold mb-4 text-cyan-400">Détails de la séquence ${index + 1}</h2>
        <div class="space-y-4">
            <div>
                <h3 class="font-bold text-green-400">En-tête:</h3>
                <p class="text-sm bg-gray-800 p-2 rounded break-all">${seq.header}</p>
            </div>
            <div>
                <h3 class="font-bold text-purple-400">Informations:</h3>
                <div class="grid grid-cols-2 gap-4 text-sm">
                    <div>Longueur: ${seq.length} bp</div>
                    <div>Type: ${seq.type}</div>
                    <div>GC Content: ${calculateGCContent(seq.sequence).toFixed(1)}%</div>
                    <div>Complexité: ${(calculateSequenceComplexity(seq.sequence) * 100).toFixed(1)}%</div>
                </div>
            </div>
            <div>
                <h3 class="font-bold text-cyan-400">Séquence complète:</h3>
                <div class="sequence-viewer h-40 overflow-y-auto text-xs font-mono">
                    ${formatSequence(seq.sequence, 'dna')}
                </div>
            </div>
        </div>
    </div>
`;

document.body.appendChild(modal);
    }
}

function showFileDebugInfo() {
    const fileInput = document.getElementById('fastaFile');
    if (fileInput && fileInput.files.length > 0) {
const file = fileInput.files[0];
console.log('=== DEBUG FILE INFO ===');
console.log('Name:', file.name);
console.log('Size:', file.size);
console.log('Type:', file.type);
console.log('Last modified:', new Date(file.lastModified));

showNotification(`Fichier: ${file.name}, Taille: ${formatFileSize(file.size)}`, 'info');
    }
}


function clearFastaFile() {
    document.getElementById('fastaPreview').innerHTML = '<p class="text-gray-400">Aucun fichier sélectionné</p>';
    document.getElementById('processBtn').disabled = true;
    window.currentFastaFile = null;
    window.currentFastaSequences = [];
}

// ==========================================
// CONNEXION NCBI (supprimée - NCBI a déjà son propre onglet)
// ==========================================


// 🔹 Recherche NCBI
