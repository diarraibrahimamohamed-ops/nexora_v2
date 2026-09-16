/**
 * Configuration centralisée pour Nexora
 * Variables et constantes globales
 */

const CONFIG = {
    API: {
        BASE_URL: '/api/v1',
        ENDPOINTS: {
            UPLOAD_FASTA: '/analysis/sequence',
            SEARCH_NCBI: '/ncbi/search',
            FETCH_SEQUENCE: '/ncbi/fetch',
            SAVE_ANALYSIS: '/analysis/save',
            INTERPRET_ANALYSIS: '/analysis/interpret',
            GET_HISTORY: '/analysis/history',
            GET_REPORT: '/analysis/{id}'
        },
        TIMEOUT: 30000
    },

    CACHE: {
        NCBI_TTL: 3600,      // 1 heure
        ANALYSIS_TTL: 1800,  // 30 minutes
        SEQUENCE_TTL: 7200   // 2 heures
    },

    UI: {
        CHART_COLORS: {
            primary: '#00ff88',
            secondary: '#0088ff',
            accent: '#ff4444',
            background: 'rgba(0, 255, 136, 0.1)'
        },
        ANIMATION_DURATION: 300,
        DEBOUNCE_DELAY: 250
    },

    BIO: {
        SEQUENCE_CHUNK_SIZE: 10000,
        MAX_SEQUENCE_LENGTH: 1000000,
        DEFAULT_GC_THRESHOLD: 50
    }
};

// Constantes biologiques
const GENETIC_CODE = {
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

const AMINO_ACID_PROPERTIES = {
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

// Export pour les modules ES6
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { CONFIG, GENETIC_CODE, AMINO_ACID_PROPERTIES };
}