/**
 * frontend/js/nexora-core.js — Module principal Nexora v2
 * Migré depuis g.js de l'ancienne version(Nexora v1) — URLs PHP remplacées par FastAPI.
 *
 * Mapping des endpoints:
 *   s.php                              → POST /api/v1/analysis/sequence
 *   api.php?action=search_ncbi         → GET  /api/v1/sequences/ncbi
 *   api.php?action=fetch_ncbi_sequence → GET  /api/v1/sequences/ncbi/fetch
 *   api.php?action=save_analysis       → POST /api/v1/analysis/save
 *   api.php?action=start_docking       → POST /api/v1/docking/submit
 *   ligands_api.php?action=*           → GET/POST /api/v1/ligands/*
 *   get_analyses.php                   → GET  /api/v1/analysis/history
 */

// Initialisation de afribioCore sera faite après le chargement complet du script
// pour éviter les conflits de définition de classe

// Vider immédiatement le localStorage des recherches NCBI pour éviter l'affichage dans Import FASTA
localStorage.removeItem('recentNCBISearches');

// ♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣
// COMPATIBILITÉ AVEC LES ANCIENS APPELS GLOBAUX
// ♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣

// Attendre que les modules soient chargés
document.addEventListener('DOMContentLoaded', function() {
    // Délai pour s'assurer que les modules sont chargés
    setTimeout(function() {
        // Fonctions de compatibilité pour les anciens appels
        window.updateProgressStep = function(step, status, message) {
            if (typeof nexoraApp !== 'undefined' && nexoraApp.emit) {
                nexoraApp.emit('progress:update', { step, status, message });
            }
        };

        window.showNotification = function(message, type = 'info', duration = 5000) {
            if (typeof nexoraApp !== 'undefined' && nexoraApp.emit) {
                nexoraApp.emit('notification:show', { message, type, duration });
            }
        };

        console.log(' Compatibilité anciens appels activée');
    }, 100);
});

// ♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣
// ANCIEN CODE G.JS (PRÉSERVÉ)
// ♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣


// Variables globales 
let dnaScene, dnaCamera, dnaRenderer, dnaModel;
let proteinScene, proteinCamera, proteinRenderer, proteinModel;
let nucleotideChart, rnaChart, resistanceChart, aminoAcidChart, complexityChart, ncbiStatsChart;
let currentSequence = '';
let currentReference = '';
let currentRNA = '';
let currentProtein = '';
let mutations = [];
let analysisHistory = [];
let currentUser = null;
let currentProject = null;
let ncbiCache = new Map();
let aiInterpretations = {};

// Variables FASTA et NCBI
let currentFastaFile = null;
let currentFastaSequences = [];
let ncbiSearchHistory = [];
let fastaSequences = []



// Tableau du code génétique
const geneticCode = {
    'UUU': 'F', 'UUC': 'F', 'UUA': 'L', 'UUG': 'L',
    'UCU': 'S', 'UCC': 'S', 'UCA': 'S', 'UCG': 'S',
    'UAU': 'Y', 'UAC': 'Y', 'UAA': '*', 'UAG': '*',
    'UGU': 'C', 'UGC': 'C', 'UGA': '*', 'UGG': 'W',
    'CUU': 'L', 'CUC': 'L', 'CUA': 'L', 'CUG': 'L',
    'CCU': 'P', 'CCC': 'P', 'CCA': 'P', 'CCG': 'P',
    'CAU': 'H', 'CAC': 'H', 'CAA': 'Q', 'CAG': 'Q',
    'CGU': 'R', 'CGC': 'R', 'CGA': 'R', 'CGG': 'R',
    'AUU': 'I', 'AUC': 'I', 'AUA': 'I', 'AUG': 'M',
    'ACU': 'T', 'ACC': 'T', 'ACA': 'T', 'ACG': 'T',
    'AAU': 'N', 'AAC': 'N', 'AAA': 'K', 'AAG': 'K',
    'AGU': 'S', 'AGC': 'S', 'AGA': 'R', 'AGG': 'R',
    'GUU': 'V', 'GUC': 'V', 'GUA': 'V', 'GUG': 'V',
    'GCU': 'A', 'GCC': 'A', 'GCA': 'A', 'GCG': 'A',
    'GAU': 'D', 'GAC': 'D', 'GAA': 'E', 'GAG': 'E',
    'GGU': 'G', 'GGC': 'G', 'GGA': 'G', 'GGG': 'G'
};

const aminoAcidProperties = {
    'A': { name: 'Alanine', mass: 89.1, type: 'hydrophobic' },
    'R': { name: 'Arginine', mass: 174.2, type: 'charged' },
    'N': { name: 'Asparagine', mass: 132.1, type: 'polar' },
    'D': { name: 'Aspartic acid', mass: 133.1, type: 'charged' },
    'C': { name: 'Cysteine', mass: 121.0, type: 'polar' },
    'E': { name: 'Glutamic acid', mass: 147.1, type: 'charged' },
    'Q': { name: 'Glutamine', mass: 146.1, type: 'polar' },
    'G': { name: 'Glycine', mass: 75.1, type: 'hydrophobic' },
    'H': { name: 'Histidine', mass: 155.2, type: 'charged' },
    'I': { name: 'Isoleucine', mass: 131.2, type: 'hydrophobic' },
    'L': { name: 'Leucine', mass: 131.2, type: 'hydrophobic' },
    'K': { name: 'Lysine', mass: 146.2, type: 'charged' },
    'M': { name: 'Methionine', mass: 149.2, type: 'hydrophobic' },
    'F': { name: 'Phenylalanine', mass: 165.2, type: 'hydrophobic' },
    'P': { name: 'Proline', mass: 115.1, type: 'hydrophobic' },
    'S': { name: 'Serine', mass: 105.1, type: 'polar' },
    'T': { name: 'Threonine', mass: 119.1, type: 'polar' },
    'W': { name: 'Tryptophan', mass: 204.2, type: 'hydrophobic' },
    'Y': { name: 'Tyrosine', mass: 181.2, type: 'polar' },
    'V': { name: 'Valine', mass: 117.1, type: 'hydrophobic' }
};

// Données scientifiques pour chaque antibiotique
const antibioticData = {
    penicillin: { target: "Paroi bactérienne (PBPs)", mechanism: "β-lactamase", relatedMutations: [2, 5, 15, 22, 35] },
    tetracycline: { target: "Sous-unité ribosomale 30S", mechanism: "Pompe d'efflux / Protection ribosomale", relatedMutations: [7, 12, 18, 22, 30] },
    chloramphenicol: { target: "Sous-unité ribosomale 50S", mechanism: "Acétyltransférase", relatedMutations: [3, 10, 20, 25, 38] },
    streptomycin: { target: "Sous-unité ribosomale 30S", mechanism: "Modification enzymatique", relatedMutations: [1, 5, 14, 22, 39] },
    rifampicin: { target: "ARN polymérase", mechanism: "Modification de la cible", relatedMutations: [4, 11, 23, 27, 36] },
    vancomycin: { target: "Paroi bactérienne (D-Ala-D-Ala)", mechanism: "Modification de la cible", relatedMutations: [6, 15, 28, 34, 40] }
};

//Ampicilne, Gentamicin,Ciproflaxine,Erythromycine, Trimethoprim( à mettre pour plus d'enrichissement)


// ♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣
// INITIALISATION PRINCIPALE
// ♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣♣
/**
 * NEXORA v2 — noyau partagé.
 * Les fonctionnalités sont séparées en modules classiques pour préserver
 * la compatibilité avec le HTML existant et les appels globaux historiques.
 */

// Initialisation principale — exécutée uniquement après chargement du DOM.
document.addEventListener('DOMContentLoaded', function() {
    console.log('Initialisation de N3XORA Plateforme...');
    initializeAll();
    loadUserData();
    setupEventListeners();

    const sampleSequences = {
        dna: 'ATGAAACGCATTAGCACCACCATTACCACCACCATCACCATTACCACAGGTAACGGTGCGGGCTGAACGTACGAATTCGAGCTCGGTACCCGGGGATCCTCTAGAGTCGACCTGCAGGCATGCAAGCTTGGCACTGGCCGTCGTTTTACAACGTCGTGACTGGGAAAACCCTGGCGTTACCCAACTTAATCGCCTTGCAGCACATCCCCCTTTCGCCAGCTGGCGTAATAGCGAAGAGGCCCGCACCGATCGCCCTTCCCAACAGTTGCGCAGCCTGAATGGCGAATGGCGCCTGATGCGGTATTTTCTCCTTACGCATCTGTGCGGTATTTCACACCGCATATGGTGCACTCTCAGTACAATCTGCTCTGATGCCGCATAGTTAAGCCAGCCCCGACACCCGCCAACACCCGCTGACGCGCCCTGACGGGCTTGTCTGCTCCCGGCATCCGCTTACAGACAAGCTGTGACCGTCTCCGGGAGCTGCATGTGTCAGAGGTTTTCACCGTCATCACCGAAACGCGCGA',
        reference: 'ATGAAACGCATTAGCACCACCATTACCACCACCATCACCATTACCACAGGTAACGGTGCGGGCTGAACGTACGAATTCGAGCTCGGTACCCGGGGATCCTCTAGAGTCGACCTGCAGGCATGCAAGCTTGGCACTGGCCGTCGTTTTACAACGTCGTGACTGGGAAAACCCTGGCGTTACCCAACTTAATCGCCTTGCAGCACATCCCCCTTTCGCCAGCTGGCGTAATAGCGAAGAGGCCCGCACCGATCGCCCTTCCCAACAGTTGCGCAGCCTGAATGGCGAATGGCGCCTGATGCGGTATTTTCTCCTTACGCATCTGTGCGGTATTTCACACCGCATATGGTGCACTCTCAGTACAATCTGCTCTGATGCCGCATAGTTAAGCCAGCCCCGACACCCGCCAACACCCGCTGACGCGCCCTGACGGGCTTGTCTGCTCCCGGCATCCGCTTACAGACAAGCTGTGACCGTCTCCGGGAGCTGCATGTGTCAGAGGTTTTCACCGTCATCACCGAAACGCGCGA'
    };

    const seqEl = document.getElementById('sequenceInput');
    const refEl = document.getElementById('referenceInput');
    if (seqEl) seqEl.value = sampleSequences.dna;
    if (refEl) refEl.value = sampleSequences.reference;

    setTimeout(() => {
        analyzeSequence();
        showNotification(' Plateforme initialisée avec succès!', 'success');
    }, 1000);
});

function initializeAll() {
    initializeMatrixBackground();
    initializeDNAVisualization();
    initializeProteinVisualization();
    initializeCharts();
    initializeFASTAUploader();
    initializeAIAssistant();
    startDataStream();
    createFloatingParticles();
    setupAdvancedFeatures();
}

// Nettoyage NCBI au démarrage : conservé hors des modules fonctionnels.
document.addEventListener('DOMContentLoaded', function() {
    if (document.getElementById('upload') && document.getElementById('upload').classList.contains('active')) {
        localStorage.removeItem('recentNCBISearches');
        const recentContainer = document.getElementById('recentNCBISearches');
        if (recentContainer) {
            recentContainer.innerHTML = '<div class="text-gray-500 text-sm">Aucune recherche récente♣</div>';
        }
    }
});

console.log('NEXORA v2 — modules frontend chargés');
