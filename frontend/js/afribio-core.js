class NexoraAfriBioCore {
    constructor() {
this.lastAlignment = null;
this.lastVariants = [];
this.lastVCF = "";
this.scoreChart = null;
this.matrixChart = null;
    }

    init() {
console.log('[AfriBio-Core] Initialisation du module frontend v2026...');
this.bindEvents();
this.createSubstitutionMatrix();
    }

    bindEvents() {
const seqInput = document.getElementById('sequenceInput');
const refInput = document.getElementById('referenceInput');

if (seqInput) {
    seqInput.addEventListener('input', () => this.updateLiveStats());
}
if (refInput) {
    refInput.addEventListener('input', () => this.updateLiveStats());
}
    }

    updateLiveStats() {
const seq = (document.getElementById('sequenceInput')?.value || '').trim().toUpperCase();
const ref = (document.getElementById('referenceInput')?.value || '').trim().toUpperCase();

const cleanSeq = seq.replace(/[^ACGTN]/g, '');
const cleanRef = ref.replace(/[^ACGTN]/g, '');

const seqLenEl = document.getElementById('seqLength');
const gcEl = document.getElementById('gcContent');

if (seqLenEl) seqLenEl.textContent = cleanSeq.length;
if (gcEl) {
    const gc = cleanSeq.length ? (((cleanSeq.match(/[GC]/g) || []).length / cleanSeq.length) * 100).toFixed(1) : '0';
    gcEl.textContent = `${gc}%`;
}

const queryLenEl = document.getElementById('afribioQueryLength');
const queryGcEl = document.getElementById('afribioQueryGC');
const refLenEl = document.getElementById('afribioRefLength');
const refGcEl = document.getElementById('afribioRefGC');

if (queryLenEl) queryLenEl.textContent = cleanSeq.length;
if (queryGcEl) {
    const qgc = cleanSeq.length ? (((cleanSeq.match(/[GC]/g) || []).length / cleanSeq.length) * 100).toFixed(1) : '0';
    queryGcEl.textContent = `${qgc}%`;
}
if (refLenEl) refLenEl.textContent = cleanRef.length;
if (refGcEl) {
    const rgc = cleanRef.length ? (((cleanRef.match(/[GC]/g) || []).length / cleanRef.length) * 100).toFixed(1) : '0';
    refGcEl.textContent = `${rgc}%`;
}
    }

    async runAfriBioAlignment() {
const query = (document.getElementById('sequenceInput')?.value || '').trim();
const reference = (document.getElementById('referenceInput')?.value || '').trim();

if (!query || !reference) {
    if (typeof showNotification === 'function') {
        showNotification("Veuillez entrer une séquence Query et une séquence de Référence", 'warning');
    } else {
        alert("Veuillez entrer une séquence Query et une séquence de Référence");
    }
    return;
}

const match = parseInt(document.getElementById('afribioMatchScore')?.value || '5', 10);
const mismatch = parseInt(document.getElementById('afribioMismatchPenalty')?.value || '-4', 10);
const gapOpen = parseInt(document.getElementById('afribioGapOpening')?.value || '-10', 10);
const gapExtend = parseInt(document.getElementById('afribioGapExtension')?.value || '-1', 10);
const seqType = document.getElementById('afribioSeqType')?.value || 'DNA';

if (typeof showNotification === 'function') {
    showNotification("Calcul de l'alignement Smith-Waterman Gotoh en cours...", 'info');
}

try {
    const payload = {
        query,
        reference,
        match,
        mismatch,
        gap_open: gapOpen,
        gap_extend: gapExtend,
        seq_type: seqType,
        sample_name: "Nexora_Sample"
    };

    const resp = await window.api.afribioAlign(payload);

    if (!resp || !resp.success) {
        throw new Error(resp?.detail || "Erreur lors du calcul d'alignement");
    }

    this.lastAlignment = resp.alignment;
    this.lastVariants = resp.variants || [];
    this.lastVCF = resp.vcf || "";

    this.renderAlignmentResults(resp);
    if (typeof showNotification === 'function') {
        showNotification("Alignement Smith-Waterman terminé avec succès !", 'success');
    }
} catch (err) {
    console.error('[AfriBio-Core] Erreur alignement:', err);
    const msg = err.message || "Erreur d'alignement";
    if (typeof showNotification === 'function') {
        showNotification(msg, 'error');
    } else {
        alert(msg);
    }
}
    }

    renderAlignmentResults(res) {
const aln = res.alignment || {};
const variants = res.variants || [];
const metrics = res.metrics || {};

const mutCountEl = document.getElementById('mutationCount');
const simEl = document.getElementById('similarity');
const optScoreEl = document.getElementById('afribioOptimalScore');
const identityEl = document.getElementById('afribioIdentityPercent');
const alignTimeEl = document.getElementById('afribioAlignTime');

if (mutCountEl) mutCountEl.textContent = metrics.variants_count ?? variants.length;
if (simEl) simEl.textContent = `${metrics.similarity ?? aln.similarity ?? 0}%`;
if (optScoreEl) optScoreEl.textContent = metrics.score ?? aln.score ?? 0;
if (identityEl) identityEl.textContent = `${metrics.identity ?? aln.identity ?? 0}%`;
if (alignTimeEl) alignTimeEl.textContent = `Python (${res.mode || 'sync'})`;

const alnViewer = document.getElementById('alignmentViewer');
if (alnViewer) {
    const qStr = aln.aligned_query || '';
    const rStr = aln.aligned_reference || '';
    const mStr = aln.alignment_string || '';

    alnViewer.innerHTML = `
        <div class="space-y-2">
            <div>
                <span class="text-cyan-400 font-bold">Query (Séquence):</span>
                <div class="font-mono text-xs text-green-400 bg-gray-900 p-2 rounded break-all tracking-widest">${qStr}</div>
            </div>
            <div>
                <span class="text-cyan-400 font-bold">Alignement (|:Match, .:Mismatch, Gap):</span>
                <div class="font-mono text-xs text-yellow-400 bg-gray-900 p-2 rounded break-all tracking-widest">${mStr}</div>
            </div>
            <div>
                <span class="text-cyan-400 font-bold">Référence:</span>
                <div class="font-mono text-xs text-blue-400 bg-gray-900 p-2 rounded break-all tracking-widest">${rStr}</div>
            </div>
            <div class="mt-3 text-xs text-gray-400 flex space-x-4">
                <span>Score: <strong class="text-cyan-400">${aln.score}</strong></span>
                <span>Identité: <strong class="text-purple-400">${aln.identity}%</strong></span>
                <span>Similarité: <strong class="text-green-400">${aln.similarity}%</strong></span>
                <span>Couverture: <strong class="text-yellow-400">${aln.coverage}%</strong></span>
            </div>
        </div>
    `;
}

const varViewer = document.getElementById('variantViewer');
if (varViewer) {
    if (variants.length === 0) {
        varViewer.innerHTML = `
            <div class="text-center text-gray-400 mt-12">
                <i class="fas fa-check-circle text-4xl mb-3 text-green-400"></i>
                <p>Aucun variant détecté (séquences identiques ou sans mutations)</p>
            </div>
        `;
    } else {
        varViewer.innerHTML = `
            <div class="space-y-2 max-h-60 overflow-y-auto">
                ${variants.map((v, idx) => `
                    <div class="p-2 bg-gray-900 border border-gray-700 rounded text-xs flex justify-between items-center">
                        <div>
                            <span class="font-bold text-cyan-400">Var #${idx + 1} (Pos ${v.position})</span>
                            <span class="ml-2 ${v.type === 'SNP' ? 'text-yellow-400' : 'text-red-400'} font-semibold">[${v.type}]</span>
                            <div class="text-gray-300">${v.reference} &rarr; ${v.alternate} ${v.transition ? '<span class="text-purple-400">(Transition)</span>' : ''}</div>
                        </div>
                        <div class="text-right">
                            <span class="text-gray-400">Qual: ${v.quality}</span>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    }
}

const exportBtn = document.getElementById('afribioExportVcfBtn');
if (exportBtn) exportBtn.disabled = variants.length === 0;

this.createScoreDistributionChart(aln);
    }

    createScoreDistributionChart(aln) {
const canvas = document.getElementById('scoreDistributionChart');
if (!canvas || typeof Chart === 'undefined') return;

if (this.scoreChart) this.scoreChart.destroy();

const alnStr = aln.alignment_string || '';
const scores = [];
for (let i = 0; i < alnStr.length; i++) {
    if (alnStr[i] === '|') scores.push(5);
    else if (alnStr[i] === '.') scores.push(-4);
    else scores.push(-1);
}

const ctx = canvas.getContext('2d');
this.scoreChart = new Chart(ctx, {
    type: 'bar',
    data: {
        labels: scores.map((_, i) => i + 1),
        datasets: [{
            label: 'Score par position',
            data: scores,
            backgroundColor: scores.map(s => s > 0 ? 'rgba(34, 197, 94, 0.8)' : 'rgba(239, 68, 68, 0.8)'),
            borderColor: scores.map(s => s > 0 ? 'rgba(34, 197, 94, 1)' : 'rgba(239, 68, 68, 1)'),
            borderWidth: 1
        }]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { labels: { color: '#e2e8f0' } }
        },
        scales: {
            x: { ticks: { display: false }, grid: { color: 'rgba(255, 255, 255, 0.1)' } },
            y: { ticks: { color: '#e2e8f0' }, grid: { color: 'rgba(255, 255, 255, 0.1)' } }
        }
    }
});
    }

    createSubstitutionMatrix() {
const canvas = document.getElementById('substitutionMatrixChart');
if (!canvas || typeof Chart === 'undefined') return;

if (this.matrixChart) this.matrixChart.destroy();

const nucs = ['A', 'T', 'G', 'C'];
const matrix = [
    [5, -4, -4, -4],
    [-4, 5, -4, -4],
    [-4, -4, 5, -4],
    [-4, -4, -4, 5]
];

const data = [];
for (let i = 0; i < matrix.length; i++) {
    for (let j = 0; j < matrix[i].length; j++) {
        data.push({
            x: j,
            y: i,
            r: Math.abs(matrix[i][j]) / 2 + 3,
            v: matrix[i][j]
        });
    }
}

const ctx = canvas.getContext('2d');
this.matrixChart = new Chart(ctx, {
    type: 'bubble',
    data: {
        datasets: [{
            label: 'Matrice de Substitution (NUC.4.4)',
            data: data,
            backgroundColor: context => (context.raw.v > 0 ? 'rgba(34, 197, 94, 0.7)' : 'rgba(239, 68, 68, 0.7)'),
            borderColor: context => (context.raw.v > 0 ? 'rgba(34, 197, 94, 1)' : 'rgba(239, 68, 68, 1)'),
            borderWidth: 1
        }]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
            tooltip: {
                callbacks: {
                    title: ctx => `${nucs[ctx[0].raw.y]} → ${nucs[ctx[0].raw.x]}`,
                    label: ctx => `Score: ${ctx.raw.v}`
                }
            }
        },
        scales: {
            x: { labels: nucs, ticks: { color: '#e2e8f0' }, grid: { color: 'rgba(255, 255, 255, 0.1)' }, min: -0.5, max: 3.5 },
            y: { labels: nucs, ticks: { color: '#e2e8f0' }, grid: { color: 'rgba(255, 255, 255, 0.1)' }, min: -0.5, max: 3.5 }
        }
    }
});
    }

    async runPhylogeneticAnalysis() {
const seq = (document.getElementById('sequenceInput')?.value || '').trim();
const ref = (document.getElementById('referenceInput')?.value || '').trim();

if (!seq) {
    if (typeof showNotification === 'function') {
        showNotification("Entrez au moins une séquence pour la phylogénie", 'warning');
    }
    return;
}

const sequences = [
    { name: "Séquence_Query", sequence: seq },
    { name: "Séquence_Ref", sequence: ref || "ATCGATCGATCGATCG" },
    { name: "Variant_Alpha", sequence: seq.replace(/A/g, 'G') || "ATCGGTCGATCGATCG" },
    { name: "Variant_Beta", sequence: seq.replace(/C/g, 'T') || "ATTGATCGATTGATCG" }
];

try {
    if (typeof showNotification === 'function') {
        showNotification("Construction de l'arbre phylogénétique Neighbor-Joining...", 'info');
    }

    const resp = await window.api.afribioPhylo({ sequences });
    if (!resp || !resp.success) throw new Error(resp?.detail || "Erreur phylogénétique");

    const phyloContainer = document.getElementById('phylogeneticTreeViewer');
    if (phyloContainer && resp.tree && resp.tree.svg) {
        phyloContainer.innerHTML = resp.tree.svg;
    }

    if (typeof showNotification === 'function') {
        showNotification(`Arbre phylogénétique NJ généré (${sequences.length} séquences)`, 'success');
    }
} catch (err) {
    console.error('[AfriBio-Core] Erreur phylogénie:', err);
    if (typeof showNotification === 'function') {
        showNotification("Erreur phylogénie: " + err.message, 'error');
    }
}
    }

    exportVCF() {
if (!this.lastVCF) {
    if (typeof showNotification === 'function') {
        showNotification("Aucun VCF disponible à exporter. Lancez d'abord un alignement.", 'warning');
    }
    return;
}

const blob = new Blob([this.lastVCF], { type: 'text/plain;charset=utf-8' });
const url = URL.createObjectURL(blob);
const a = document.createElement('a');
a.href = url;
a.download = `afribio_variants_${Date.now()}.vcf`;
a.click();
URL.revokeObjectURL(url);

if (typeof showNotification === 'function') {
    showNotification("Fichier VCF 4.2 exporté avec succès !", 'success');
}
    }
}

// Définition précoce pour éviter les ReferenceError si index.html essaie d'accéder trop tôt
window.afribioCore = window.afribioCore || {};

// Initialisation immédiate de afribioCore après la définition de la classe
window.afribioCore = new NexoraAfriBioCore();

// Initialisation au chargement du DOM
document.addEventListener('DOMContentLoaded', () => {
    if (window.afribioCore) window.afribioCore.init();
});
