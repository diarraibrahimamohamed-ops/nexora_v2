/**
 * Module de gestion des ligands validés — Nexora v2
 * Branché sur FastAPI: GET /api/v1/ligands, /search, /{id}/validate
 * Remplace ligands_api.php (URLs PHP cassées: ligands_/api/v1/... de l'ancienne version)
 */

let validatedLigands = [];
let selectedLigand = null;

//initialisation du module
function initLigandsModule() {
    console.log('[Ligands] Initialisation module v2...');
    loadValidatedLigands();
    setupLigandEventListeners();
}

/**
 * Charge les ligands validés.
 * Ancienne version: ligands_api.php?action=get_validated_ligands
 * Nouveau: GET /api/v1/ligands?limit=100
 */
async function loadValidatedLigands() {
    const select = document.getElementById('validatedLigandSelect');
    if (!select) {
        console.error('[Ligands] Element validatedLigandSelect non trouvé');
        return;
    }

    try {
        console.log('[Ligands] Chargement...');
        select.innerHTML = '<option value="">Chargement des ligands...</option>';

        let data;
        if (window.api && typeof window.api.getLigands === 'function') {
            data = await window.api.getLigands({ limit: 100 });
        } else {
            const r = await fetch('/api/v1/ligands?limit=100');
            if (!r.ok) throw new Error('Erreur serveur ligands (' + r.status + ')');
            data = await r.json();
        }

        if (data.success && Array.isArray(data.ligands)) {
            validatedLigands = data.ligands;
            populateLigandSelect(data.ligands);
            console.log(`[Ligands] ${data.ligands.length} ligands chargés`);
        } else if (Array.isArray(data)) {
            validatedLigands = data;
            populateLigandSelect(data);
        } else {
            console.error('[Ligands] Réponse inattendue:', data);
            select.innerHTML = '<option value="">Erreur de chargement</option>';
        }
    } catch (error) {
        console.error('[Ligands] Erreur chargement:', error);
        if (select) {
            select.innerHTML = '<option value="">Erreur de connexion</option>';
        }
    }
}

function populateLigandSelect(ligands) {
    const select = document.getElementById('validatedLigandSelect');
    if (!select) return;

    const categories = {};
    ligands.forEach((ligand) => {
        const cat = ligand.category || 'other';
        if (!categories[cat]) categories[cat] = [];
        categories[cat].push(ligand);
    });

    select.innerHTML = '<option value="">Sélectionnez un ligand validé...</option>';

    Object.keys(categories).sort().forEach((category) => {
        const optgroup = document.createElement('optgroup');
        optgroup.label = getCategoryDisplayName(category);

        categories[category].forEach((ligand) => {
            const option = document.createElement('option');
            option.value = ligand.id;
            let displayName = ligand.name || ('Ligand ' + ligand.id);
            if (ligand.status === 'approved') displayName += ' ✓';
            else if (ligand.status === 'clinical_trial') displayName += ' ';
            if (ligand.drug_likeness_score >= 0.8) displayName += ' ★';
            option.textContent = displayName;
            option.dataset.smiles = ligand.smiles || '';
            optgroup.appendChild(option);
        });

        select.appendChild(optgroup);
    });
}

// catégories de ligants
function getCategoryDisplayName(category) {
    const names = {
        analgesic: ' Analgésiques',
        'anti-inflammatory': ' Anti-inflammatoires',
        antibiotic: ' Antibiotiques',
        antiviral: ' Antiviraux',
        antineoplastic: ' Antinéoplasiques',
        kinase_inhibitor: ' Inhibiteurs de Kinases',
        protease_inhibitor: '⚡ Inhibiteurs de Protéases',
        stimulant: ' Stimulants',
        opioid: ' Opioides',
        benzodiazepine: ' Benzodiazépines',
        flavonoid: ' Flavonoïdes',
        polyphenol: ' Polyphénols',
        neurotransmitter: ' Neurotransmetteurs',
        vitamin: ' Vitamines',
        steroid: ' Stéroïdes',
        nucleotide: ' Nucléotides',
        cofactor: ' Cofacteurs',
    };
    return names[category] || ` ${category}`;
}

function setupLigandEventListeners() {
    const ligandSelect = document.getElementById('validatedLigandSelect');
    if (!ligandSelect) return;

    ligandSelect.addEventListener('change', function () {
        const ligandId = this.value;
        if (ligandId) selectLigand(ligandId);
        else clearLigandSelection();
    });
}

function selectLigand(ligandId) {
    const ligand = validatedLigands.find((l) => String(l.id) === String(ligandId));
    if (!ligand) {
        console.error('[Ligands] Ligand non trouvé:', ligandId);
        return;
    }

    selectedLigand = ligand;
    console.log('[Ligands] Sélectionné:', ligand.name);

    const smilesInput = document.getElementById('ligandSmiles');
    if (smilesInput) smilesInput.value = ligand.smiles || '';

    displayLigandInfo(ligand);
    validateLigandSelection();
}

// affichage des informations concernant le ligand selectionné
function displayLigandInfo(ligand) {
    const infoDiv = document.getElementById('ligandInfo');
    if (!infoDiv) return;

    const catEl = document.getElementById('ligandCategory');
    const weightEl = document.getElementById('ligandWeight');
    const logpEl = document.getElementById('ligandLogP');
    const scoreEl = document.getElementById('ligandScore');
    const descEl = document.getElementById('ligandDescription');
    const warningsDiv = document.getElementById('ligandWarnings');

    if (catEl) catEl.textContent = getCategoryDisplayName(ligand.category);
    if (weightEl) weightEl.textContent = `${ligand.molecular_weight} Da`;
    if (logpEl) logpEl.textContent = ligand.logp;
    if (scoreEl) {
        const s = Number(ligand.drug_likeness_score);
        scoreEl.textContent = Number.isFinite(s) ? `${(s * 100).toFixed(1)}%` : 'N/A';
    }
    if (descEl) descEl.textContent = ligand.description || 'Aucune description disponible';

    const warnings = [];
    if (ligand.lipinski_violations && ligand.lipinski_violations.length > 0) {
        warnings.push(`Violations Lipinski: ${ligand.lipinski_violations.join(', ')}`);
    }
    if (ligand.status === 'research') {
        warnings.push(' Ligand expérimental — validation requise');
    }
    if (ligand.drug_likeness_score < 0.4) {
        warnings.push(' Faible drug-likeness');
    }
    if (warningsDiv) {
        warningsDiv.innerHTML = warnings.length > 0 ? warnings.join('<br>') : ' Aucun avertissement';
    }

    infoDiv.classList.remove('hidden');
    infoDiv.classList.remove('border-green-400', 'border-yellow-400', 'border-red-400');
    if (ligand.status === 'approved') infoDiv.classList.add('border-green-400');
    else if (ligand.status === 'clinical_trial') infoDiv.classList.add('border-yellow-400');
    else infoDiv.classList.add('border-red-400');
}

function clearLigandSelection() {
    selectedLigand = null;
    const smilesInput = document.getElementById('ligandSmiles');
    if (smilesInput) smilesInput.value = '';
    const infoDiv = document.getElementById('ligandInfo');
    if (infoDiv) infoDiv.classList.add('hidden');
    const validationDiv = document.getElementById('ligandValidation');
    if (validationDiv) validationDiv.remove();
}

/**
 * Valide ligand + analyse.
 * Ancienne version: ligands_api.php?action=validate_ligand_selection
 * Nouveau: POST /api/v1/ligands/{id}/validate?analysis_id=
 */
async function validateLigandSelection() {
    if (!selectedLigand) return;

    const analysisId = (typeof currentAnalysisId !== 'undefined' && currentAnalysisId)
        ? currentAnalysisId
        : (document.getElementById('dockingAnalysisSelect')?.value || null);

    if (!analysisId) return;

    try {
        console.log('[Ligands] Validation ligand-analyse...');
        let result;
        if (window.api && typeof window.api.validateLigand === 'function') {
            result = await window.api.validateLigand(selectedLigand.id, analysisId);
        } else {
            const r = await fetch(
                `/api/v1/ligands/${selectedLigand.id}/validate?analysis_id=${encodeURIComponent(analysisId)}`,
                { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }
            );
            if (!r.ok) throw new Error('Validation échouée');
            result = await r.json();
        }

        if (result.success && result.validation) {
            displayValidationResult(result.validation);
        }
    } catch (error) {
        console.error('[Ligands] Erreur validation:', error);
    }
}

function displayValidationResult(validation) {
    let validationDiv = document.getElementById('ligandValidation');
    if (!validationDiv) {
        validationDiv = document.createElement('div');
        validationDiv.id = 'ligandValidation';
        validationDiv.className = 'bg-gray-800 bg-opacity-50 p-4 rounded mt-4';
        const ligandInfo = document.getElementById('ligandInfo');
        if (ligandInfo && ligandInfo.parentNode) {
            ligandInfo.parentNode.insertBefore(validationDiv, ligandInfo.nextSibling);
        }
    }

    const score = validation.compatibility_score ?? 0;
    const scoreClass = score >= 80 ? 'text-green-400' : (score >= 60 ? 'text-yellow-400' : 'text-red-400');
    const scoreText = score >= 80 ? 'Excellent' : (score >= 60 ? 'Bon' : 'Modéré');
    const status = (validation.ligand_info && validation.ligand_info.status) || '';

    validationDiv.innerHTML = `
        <h5 class="font-bold text-cyan-400 mb-2">
            <i class="fas fa-check-circle mr-2"></i>Validation Scientifique
        </h5>
        <div class="grid grid-cols-2 gap-4 mb-3">
            <div>
                <span class="text-sm text-gray-400">Score de compatibilité:</span>
                <div class="text-lg font-semibold ${scoreClass}">${score}/100 — ${scoreText}</div>
            </div>
            <div>
                <span class="text-sm text-gray-400">Statut ligand:</span>
                <div class="text-lg font-semibold text-cyan-400">${getLigandStatusText(status)}</div>
            </div>
        </div>
        ${validation.compatibility_notes && validation.compatibility_notes.length ? `
        <div class="mb-3">
            <h6 class="text-sm font-semibold text-blue-400 mb-1">Notes de compatibilité:</h6>
            <ul class="text-xs text-gray-300 list-disc list-inside">
                ${validation.compatibility_notes.map((n) => `<li>${n}</li>`).join('')}
            </ul>
        </div>` : ''}
        ${validation.warnings && validation.warnings.length ? `
        <div class="mb-3">
            <h6 class="text-sm font-semibold text-yellow-400 mb-1"> Avertissements:</h6>
            <ul class="text-xs text-yellow-300 list-disc list-inside">
                ${validation.warnings.map((w) => `<li>${w}</li>`).join('')}
            </ul>
        </div>` : ''}
        ${validation.recommended_parameters ? `
        <div class="mt-3">
            <h6 class="text-sm font-semibold text-purple-400 mb-1">Paramètres recommandés:</h6>
            <div class="text-xs text-gray-300">
                <span class="mr-3">Exhaustiveness: ${validation.recommended_parameters.exhaustiveness}</span>
                <span class="mr-3">Modes: ${validation.recommended_parameters.num_modes}</span>
                <span>Range: ${validation.recommended_parameters.energy_range} kcal/mol</span>
            </div>
        </div>` : ''}
    `;
}

// status du ligand selectionné
function getLigandStatusText(status) {
    const statusTexts = {
        approved: ' Approuvé',
        clinical_trial: ' Essai clinique',
        experimental: ' Expérimental',
        research: ' Recherche',
    };
    return statusTexts[status] || status || '—';
}

async function searchLigands(query) {
    if (!query || query.length < 2) return [];
    try {
        if (window.api && typeof window.api.searchLigands === 'function') {
            const data = await window.api.searchLigands(query, 10);
            return data.results || data.ligands || [];
        }
        const r = await fetch(`/api/v1/ligands/search?q=${encodeURIComponent(query)}&limit=10`);
        const data = await r.json();
        return data.results || [];
    } catch (error) {
        console.error('[Ligands] Erreur recherche:', error);
        return [];
    }
}

window.initLigandsModule = initLigandsModule;
window.loadValidatedLigands = loadValidatedLigands;
window.selectLigand = selectLigand;
window.clearLigandSelection = clearLigandSelection;
window.validateLigandSelection = validateLigandSelection;
window.searchLigands = searchLigands;
window.selectedLigand = null;

Object.defineProperty(window, 'getSelectedLigand', {
    get() { return selectedLigand; },
});

document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('validatedLigandSelect')) {
        initLigandsModule();
    }
});
