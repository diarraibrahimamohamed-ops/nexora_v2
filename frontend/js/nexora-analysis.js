async function analyzeSequence() {
    const sequence = document.getElementById('sequenceInput').value.toUpperCase().replace(/[^ATCG]/g, '');
    const reference = document.getElementById('referenceInput').value.toUpperCase().replace(/[^ATCG]/g, '');
    
    if (!sequence) {
showNotification('Veuillez entrer une séquence ADN valide', 'error');
return;
    }
    

    currentSequence = sequence;
    currentReference = reference || sequence;

    // Afficher progression
    showAnalysisProgress();

  
    
    // Analyses séquentielles avec progress
    await performAnalysisSteps(sequence, reference);
      // Update statistics
    updateStatistics(sequence, reference);
    
    // Analyze nucleotides
    analyzeNucleotides(sequence);
    
    // Detect mutations
    detectMutations(sequence, reference);
    
    // Update sequence viewers
    updateSequenceViewer(sequence);
    
    // Transcription and translation
    await transcribeAndTranslate(sequence);
    
    // Analyze resistance
    analyzeResistanceProfile();

    // Update 3D models
    update3DModels();
          
    
    
    // Update counters
    updateCounters();
    // Sauvegarder l'analyse
    await saveAnalysisToDatabase();
    
    // Activer le bouton IA
    document.getElementById('aiconclusion').disabled = false;
    updateAIAnalysisStatus();
    
    
}

async function analyserBackend(sequence) {

    const response = await fetch('/api/v1/analysis/sequence', {
method: "POST",
headers: { "Content-Type": "application/json" },
body: JSON.stringify({ sequence: sequence })
    });

    const data = await response.json();
    return data;
}

function showAnalysisProgress() {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.id = 'analysisProgressModal';
    modal.style.display = 'block';
    
    modal.innerHTML = `
<div class="modal-content" style="max-width: 600px;">
    <h2 class="text-xl font-bold mb-4 text-cyan-400">
        <i class="fas fa-cogs mr-2"></i>
        Analyse en cours...
    </h2>
    <div id="analysisSteps" class="space-y-4">
        ${createProgressStep('1', 'Validation séquence', 'pending')}
        ${createProgressStep('2', 'Analyse nucléotides', 'pending')}
        ${createProgressStep('3', 'Détection mutations', 'pending')}
        ${createProgressStep('4', 'Transcription ARN', 'pending')}
        ${createProgressStep('5', 'Traduction protéine', 'pending')}
        ${createProgressStep('6', 'Analyse résistance', 'pending')}
        ${createProgressStep('7', 'Modèles 3D', 'pending')}
        
    </div>
    <div class="mt-6">
        <div class="w-full bg-gray-700 rounded-full h-3">
            <div id="overallProgress" class="bg-gradient-to-r from-cyan-400 to-magenta-400 h-3 rounded-full transition-all duration-500" 
                 style="width: 0%"></div>
        </div>
        <p class="text-center mt-2 text-sm text-gray-400">
            <span id="currentStep">Initialisation...</span>
        </p>
    </div>
</div>
    `;
    
    document.body.appendChild(modal);
}

function createProgressStep(number, title, status) {
    const icons = {
'pending': 'fas fa-circle text-gray-400',
'running': 'fas fa-spinner fa-spin text-cyan-400',
'completed': 'fas fa-check-circle text-green-400',
'error': 'fas fa-times-circle text-red-400'
    };
    
    return `
<div id="step${number}" class="flex items-center p-3 rounded ${status === 'running' ? 'bg-cyan-400 bg-opacity-20' : 'bg-gray-800'}">
    <div class="w-8 h-8 rounded-full bg-gray-700 flex items-center justify-center mr-4">
        <span class="text-sm font-bold">${number}</span>
    </div>
    <div class="flex-1">
        <span class="font-semibold">${title}</span>
    </div>
    <i id="stepIcon${number}" class="${icons[status]}"></i>
</div>
    `;
}

function updateProgressStep(stepNumber, status, title = null) {
    const step = document.getElementById(`step${stepNumber}`);
    const icon = document.getElementById(`stepIcon${stepNumber}`);
    const currentStepText = document.getElementById('currentStep');
    const overallProgress = document.getElementById('overallProgress');
    
    if (!step) return;
    
    const icons = {
'pending': 'fas fa-circle text-gray-400',
'running': 'fas fa-spinner fa-spin text-cyan-400',
'completed': 'fas fa-check-circle text-green-400',
'error': 'fas fa-times-circle text-red-400'
    };
    
    // Réinitialiser les classes
    step.className = `flex items-center p-3 rounded ${status === 'running' ? 'bg-cyan-400 bg-opacity-20' : 'bg-gray-800'}`;
    icon.className = icons[status];
    
    if (title) {
currentStepText.textContent = title;
    }
    
    // Mettre à jour la barre de progression globale
    const progress = (stepNumber / 8) * 100;
    overallProgress.style.width = progress + '%';
}

async function performAnalysisSteps(sequence, reference) {
    const steps = [
{ fn: () => validateSequence(sequence), title: 'Validation de séquence(s)' },
{ fn: () => analyzeNucleotides(sequence), title: 'Analyse des nucléotides' },
{ fn: () => detectMutations(sequence, reference), title: 'Détection de mutation(s)' },
{ fn: () => transcribeAndTranslate(sequence), title: 'Transcription en ARN' },
{ fn: () => transcribeAndTranslate(sequence), title: 'Traduction en protéine' },
{ fn: () => analyzeResistanceProfile(), title: 'Analyse de résistance' },
{ fn: () => update3DModels(), title: 'Modélisation 3D' },
       
    ];
    
    for (let i = 0; i < steps.length; i++) {
updateProgressStep(i + 1, 'running', steps[i].title);

try {
    await new Promise(resolve => setTimeout(resolve, 800)); // Simulation délai
      steps[i].fn();
    updateProgressStep(i + 1, 'completed');
} catch (error) {
    console.error(`Error in step ${i + 1}:`, error);
    updateProgressStep(i + 1, 'error');
}
    }
    
    // Fermer le modal après 2 secondes
    setTimeout(() => {
const modal = document.getElementById('analysisProgressModal');
if (modal) modal.remove();
showNotification('Analyse complète terminée!', 'success');
    }, 1000);
}

function validateSequence(sequence) {
    return new Promise((resolve, reject) => {
if (!sequence || sequence.length < 10) {
    reject(new Error('Séquence trop courte'));
    return;
}

const validNucleotides = sequence.match(/[ATCG]/g);
if (!validNucleotides || validNucleotides.length / sequence.length < 0.9) {
    reject(new Error('Séquence contient trop de caractères invalides'));
    return;
}

resolve();
    });
}

async function saveAnalysisToDatabase() {
    if (!currentSequence) return;
    
    const analysisData = {
sequence_name: `Analyse_${new Date().toISOString().slice(0, 19).replace(/[:.]/g, '-')}`,
original_sequence: currentSequence,
reference_sequence: currentReference,
sequence_type: 'DNA',
mutations: mutations,
resistance_data: getResistanceData(),
protein_data: getProteinData(),
user_id: currentUser?.id || null,
project_id: currentProject?.id || null
    };
    
    try {
const response = await fetch(CONFIG.API.BASE_URL + CONFIG.API.ENDPOINTS.SAVE_ANALYSIS, {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('token') || sessionStorage.getItem('token')}`
    },
    body: JSON.stringify(analysisData)
});

const result = await response.json();

if (result.success) {
    window.currentAnalysisId = result.analysis_id;
    addToAnalysisHistory(analysisData, result.analysis_id);
    showNotification('Analyse sauvegardée', 'success');
} else {
    showNotification('Erreur sauvegarde: ' + (result.error || result.detail || 'Erreur inconnue'), 'error');
}
    } catch (error) {
console.error('Save analysis error:', error);
showNotification('Erreur sauvegarde', 'error');
    }
}

function getResistanceData() {
    const data = window.lastAMRResult;
    if (!data) return {};
    const hits = Array.isArray(data.hits) ? data.hits : [];
    return {
        amr: {
            evidence_level: data.evidence_level || 'none',
            method: data.method || 'not_available',
            determinant_count: hits.length,
            confidence_score: null,
            phenotype_prediction: null
        }
    };
}

function getProteinData() {
    if (!currentProtein) return null;
    const p = window.lastProteinProperties || {};
    return {
        sequence: currentProtein,
        sequence_length: p.sequence_length ?? currentProtein.length,
        molecular_weight: p.molecular_weight_da ?? null,
        isoelectric_point: p.isoelectric_point ?? null,
        gravy: p.gravy ?? null,
        hydrophobicity: p.gravy ?? null,
        aromaticity: p.aromaticity ?? null,
        instability_index: p.instability_index ?? null,
        aliphatic_index: p.aliphatic_index ?? null,
        extinction_coefficient_reduced: p.extinction_coefficient_reduced ?? null,
        extinction_coefficient_oxidized: p.extinction_coefficient_oxidized ?? null,
        secondary_structure_heuristic: p.secondary_structure_heuristic ?? null,
        secondary_alpha: null,
        predicted_function: null,
        method: p.method ?? null
    };
}

function getResistanceMechanism(antibiotic) {
    const mechanisms = {
'penicillin': 'β-lactamase',
'tetracycline': 'Efflux pump',
'chloramphenicol': 'Acetyltransferase',
'streptomycin': 'Modification enzyme',
'rifampicin': 'Target modification',
'vancomycin': 'Target modification'
    };
    return mechanisms[antibiotic] || 'Unknown mechanism';
}

// ==========================================
// FONCTIONS EXISTANTES AMÉLIORÉES
// ==========================================

function updateStatistics(sequence, reference) {
    // Mise à jour statistiques existante + nouvelles métriques
    document.getElementById('seqLength').textContent = sequence.length;
    
    const gcCount = (sequence.match(/[GC]/g) || []).length;
    const gcPercentage = ((gcCount / sequence.length) * 100).toFixed(1);
    document.getElementById('gcContent').textContent = gcPercentage + '%';
    
    let mutationCount = 0;
    mutations = [];
    
    for (let i = 0; i < Math.min(sequence.length, reference.length); i++) {
if (sequence[i] !== reference[i]) {
    mutationCount++;
    mutations.push({
        position: i,
        original: reference[i],
        mutated: sequence[i],
        type: getMutationType(reference[i], sequence[i])
    });
}
    }
    
    document.getElementById('mutationCount').textContent = mutationCount;
    
    const similarity = reference.length > 0 ? 
(((Math.min(sequence.length, reference.length) - mutationCount) / Math.min(sequence.length, reference.length)) * 100).toFixed(1) : 
100;
    document.getElementById('similarity').textContent = similarity + '%';
}

   // a revoir pour encore differencier tous les types de mutations
function getMutationType(original, mutated) {
    const transitions = ['AG', 'GA', 'CT', 'TC'];
    const transversions = ['AC', 'CA', 'AT', 'TA', 'GC', 'CG', 'GT', 'TG'];
    
    const change = original + mutated;
    if (transitions.includes(change)) return 'Transition';
    if (transversions.includes(change)) return 'Transversion';
    return 'Unknown';
}

function analyzeNucleotides(sequence) {
    const counts = { A: 0, T: 0, G: 0, C: 0 };
    
    for (let nucleotide of sequence) {
        if (counts.hasOwnProperty(nucleotide)) {
            counts[nucleotide]++;
        }
    }
    
    if (nucleotideChart) {
        nucleotideChart.data.datasets[0].data = [counts.A, counts.T, counts.G, counts.C];
        nucleotideChart.update();
    }
}

function detectMutations(sequence, reference) {
    const alertsContainer = document.getElementById('mutationAlerts');
    if (!alertsContainer) return;
    
    alertsContainer.innerHTML = '';
    
    mutations.forEach((mutation, index) => {
        const alert = document.createElement('div');
        alert.className = 'mutation-alert p-3 rounded fade-in';
        alert.innerHTML = `
            <div class="flex justify-between items-center">
                <span class="font-bold">${mutation.type}</span>
                <i class="fas fa-exclamation-circle text-red-400"></i>
            </div>
            <div class="text-sm mt-1">
                Position: ${mutation.position + 1} | ${mutation.original} → ${mutation.mutated}
            </div>
        `;
        alertsContainer.appendChild(alert);
    });
}

function updateSequenceViewer(sequence) {
    const viewer = document.getElementById('sequenceViewer');
    if (!viewer) return;
    
    let formatted = '';
    for (let i = 0; i < sequence.length; i++) {
        const nucleotide = sequence[i];
        const isMutation = mutations.some(m => m.position === i);
        const className = `nucleotide-${nucleotide.toLowerCase()}${isMutation ? ' mutation-highlight' : ''}`;
        
        formatted += `<span class="${className}">${nucleotide}</span>`;
        
        if ((i + 1) % 10 === 0) formatted += ' ';
        if ((i + 1) % 50 === 0) formatted += '<br>';
    }
    
    viewer.innerHTML = formatted;
}

async function transcribeAndTranslate(sequence) {
    // Transcription: ADN en ARN
    const rna = sequence.replace(/T/g, 'U');
    currentRNA = rna;
    
    // Update RNA display
    const dnaTemplate = document.getElementById('dnaTemplate');
    const rnaTranscript = document.getElementById('rnaTranscript');
    const matureRNA = document.getElementById('matureRNA');
    
    if (dnaTemplate) {
        dnaTemplate.innerHTML = formatSequence(sequence, 'dna');
    }
    if (rnaTranscript) {
        rnaTranscript.innerHTML = formatSequence(rna, 'rna');
    }
    if (matureRNA) {
        // Simulate mature RNA (remove introns, add 5' cap and 3' poly-A tail)
        const mature = 'CAP-' + rna + '-AAAAAAA';
        matureRNA.innerHTML = formatSequence(mature, 'rna');
    }
    
    // Translation: ARN en Proteine
    await translateRNA(rna);
    
    // Update RNA chart
    if (rnaChart) {
        const rnaCounts = { A: 0, U: 0, G: 0, C: 0 };
        for (let nucleotide of rna) {
            if (rnaCounts.hasOwnProperty(nucleotide)) {
                rnaCounts[nucleotide]++;
            }
        }
        rnaChart.data.datasets[0].data = [rnaCounts.A, rnaCounts.U, rnaCounts.G, rnaCounts.C];
        rnaChart.update();
    }
}

async function translateRNA(rna) {
    let protein = '';
    let codons = '';
    let aminoAcidNames = '';

    console.log(' TRANSLATION DEBUG:');
    console.log('ARN input:', rna);

    // Recherche d'un codon initiateur(AUG, méthionine)
    const startIndex = rna.indexOf('AUG');
    console.log('Start index (AUG):', startIndex);

    if (startIndex === -1) {
        codons = 'Aucun codon initiateur AUG trouvé';
        console.log(' No AUG found - codons set to:', codons);
        protein = '';
        aminoAcidNames = '';
        // Update displays even when no AUG found
        const codonDisplay = document.getElementById('codonSequence');
        const proteinDisplay = document.getElementById('proteinSequence');
        const aminoDisplay = document.getElementById('aminoAcidNames');

        if (codonDisplay) {
            codonDisplay.innerHTML = codons;
            console.log(' Updated codonDisplay with:', codons);
        }
        if (proteinDisplay) proteinDisplay.innerHTML = formatProteinSequence(protein);
        if (aminoDisplay) aminoDisplay.innerHTML = aminoAcidNames;
        await updateProteinProperties("");
        return;
    }

    console.log(' AUG found at position', startIndex);

    // Translate from start codon
    for (let i = startIndex; i < rna.length - 2; i += 3) {
        const codon = rna.substring(i, i + 3);
        console.log('🔍 Processing codon:', codon, 'at position', i);

        if (codon.length === 3) {
            const aminoAcid = geneticCode[codon] || 'X';
            console.log('🧬 Amino acid for', codon, ':', aminoAcid);

            if (aminoAcid === '*') {
                console.log(' Stop codon found, stopping translation');
                break; // Stop la traduction, à la rencontre d'un codon stop
            }

            protein += aminoAcid;
            codons += codon + ' ';
            console.log(' Current codons string:', codons.trim());
            console.log(' Current protein string:', protein);

            if (aminoAcidProperties[aminoAcid]) {
                aminoAcidNames += aminoAcidProperties[aminoAcid].name + ' ';
            }
        }
    }

    // If no codons were found (sequence too short)
    if (!codons) {
        codons = 'Séquence ARN trop courte pour contenir des codons complets';
        console.log(' No codons found - sequence too short');
    }

    console.log(' Final codons:', codons);
    console.log(' Final protein:', protein);

    currentProtein = protein;

    // Update displays
    const codonDisplay = document.getElementById('codonSequence');
    const proteinDisplay = document.getElementById('proteinSequence');
    const aminoDisplay = document.getElementById('aminoAcidNames');

    if (codonDisplay) {
        codonDisplay.innerHTML = codons;
        console.log(' Updated codonDisplay with:', codons);
    }
    if (proteinDisplay) {
        const formattedProtein = formatProteinSequence(protein);
        proteinDisplay.innerHTML = formattedProtein;
        console.log(' Updated proteinDisplay with formatted protein');
    }
    if (aminoDisplay) {
        aminoDisplay.innerHTML = aminoAcidNames;
        console.log(' Updated aminoDisplay with:', aminoAcidNames);
    }

    // Update protein properties
    await updateProteinProperties(protein);
    
    // Update amino acid chart
    updateAminoAcidChart(protein);
}

function formatSequence(sequence, type) {
    let formatted = '';
    for (let i = 0; i < sequence.length; i++) {
        const char = sequence[i];
        if (type === 'dna') {
            formatted += `<span class="nucleotide-${char.toLowerCase()}">${char}</span>`;
        } else if (type === 'rna') {
            formatted += `<span class="nucleotide-${char.toLowerCase()}">${char}</span>`;
        }
        
        if ((i + 1) % 10 === 0) formatted += ' ';
        if ((i + 1) % 50 === 0) formatted += '<br>';
    }
    return formatted;
}

function formatProteinSequence(protein) {
    let formatted = '';
    for (let i = 0; i < protein.length; i++) {
        const aa = protein[i];
        const props = aminoAcidProperties[aa];
        if (props) {
            formatted += `<span class="amino-acid ${props.type}" title="${props.name}">${aa}</span>`;
        } else {
            formatted += `<span class="amino-acid">${aa}</span>`;
        }
    }
    return formatted;
}

async function updateProteinProperties(protein) {
    const ids = ['proteinLength','proteinMass','proteinPI','hydrophobicity','aromaticity','instabilityIndex','aliphaticIndex','secondaryStructure','extinctionCoefficient'];
    const set = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };

    if (!protein) {
        window.lastProteinProperties = null;
        ids.forEach(id => set(id, 'n/d'));
        return;
    }

    // Initial deterministic local values keep the UI responsive while the
    // authoritative physicochemical calculation is obtained from BioPython.
    set('proteinLength', String(protein.length));
    set('proteinMass', 'calcul…');
    set('proteinPI', 'calcul…');
    set('hydrophobicity', 'calcul…');
    set('aromaticity', 'calcul…');
    set('instabilityIndex', 'calcul…');
    set('aliphaticIndex', 'calcul…');
    set('secondaryStructure', 'calcul…');
    set('extinctionCoefficient', 'calcul…');

    try {
        const result = window.api && typeof window.api.proteinProperties === 'function'
            ? await window.api.proteinProperties(protein)
            : await fetch('/api/v1/analysis/protein-properties', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(localStorage.getItem('token') || sessionStorage.getItem('token')
                        ? { 'Authorization': `Bearer ${localStorage.getItem('token') || sessionStorage.getItem('token')}` } : {})
                },
                body: JSON.stringify({ sequence: protein })
            }).then(async r => {
                const data = await r.json();
                if (!r.ok) throw new Error(data.detail || 'Calcul des propriétés protéiques indisponible');
                return data;
            });

        window.lastProteinProperties = result;
        set('proteinLength', result.sequence_length);
        set('proteinMass', `${Number(result.molecular_weight_da).toFixed(2)} Da`);
        set('proteinPI', Number(result.isoelectric_point).toFixed(2));
        set('hydrophobicity', Number(result.gravy).toFixed(3));
        set('aromaticity', Number(result.aromaticity).toFixed(3));
        set('instabilityIndex', Number(result.instability_index).toFixed(2));
        set('aliphaticIndex', Number(result.aliphatic_index).toFixed(2));
        const ss = result.secondary_structure_heuristic || {};
        set('secondaryStructure', `H ${Number(ss.helix_like_pct).toFixed(1)}% · E ${Number(ss.sheet_like_pct).toFixed(1)}% · C ${Number(ss.coil_like_pct).toFixed(1)}%`);
        set('extinctionCoefficient', `${result.extinction_coefficient_reduced} / ${result.extinction_coefficient_oxidized}`);
    } catch (error) {
        console.error('[ProteinProperties]', error);
        window.lastProteinProperties = null;
        set('proteinMass', 'n/d');
        set('proteinPI', 'n/d');
        set('hydrophobicity', 'n/d');
        set('aromaticity', 'n/d');
        set('instabilityIndex', 'n/d');
        set('aliphaticIndex', 'n/d');
        set('secondaryStructure', 'n/d');
        set('extinctionCoefficient', 'n/d');
    }
}

function updateAminoAcidChart(protein) {
    if (!aminoAcidChart || !protein) return;
    
    const counts = {};
    for (let aa of protein) {
        counts[aa] = (counts[aa] || 0) + 1;
    }
    
    const labels = Object.keys(counts);
    const data = Object.values(counts);
    
    aminoAcidChart.data.labels = labels;
    aminoAcidChart.data.datasets[0].data = data;
    aminoAcidChart.update();
}

    // Analyse de résistance par antibiotique
// Type d'organisme global (à définir via ton UI, ex: select "microorganisme" ou "humain")


