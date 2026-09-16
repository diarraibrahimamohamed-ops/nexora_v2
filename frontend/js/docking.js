/**
 * Module d'Amarrage Moléculaire Nexora v2
 * Frontend branché sur FastAPI (/api/v1/docking/* + /api/v1/analysis/*)
 * Remplace les appels PHP (get_analyses.php, api.php?action=start_docking de l'ancienne version)
 */

let currentAnalysisId = null;
let currentProteinSequence = null;
let dockingInProgress = false;
let currentDockingJobId = null;
let dockingModuleInitialized = false;

//initialisation du module 
function initDockingModule() {
    if (dockingModuleInitialized) {
        loadDockingAnalyses();
        return;
    }
    dockingModuleInitialized = true;
    console.log('[Docking] Initialisation module v2...');
    loadDockingAnalyses();
    setupDockingEventListeners();

    setTimeout(function () {
        const select = document.getElementById('dockingAnalysisSelect');
        if (select && select.options.length <= 1) {
            console.log('[Docking] Rechargement forcé des analyses...');
            loadDockingAnalyses();
        }
    }, 2000);
}

/**
 * Charge les analyses avec séquence protéique via API v2.
 * Ancien version: get_analyses.php → Nouveau: GET /api/v1/analysis/history
 */
async function loadDockingAnalyses() {
    const select = document.getElementById('dockingAnalysisSelect');
    if (!select) {
        console.error('[Docking] Element dockingAnalysisSelect non trouvé');
        return;
    }

    try {
        console.log('[Docking] Chargement analyses...');

        // Debug: Vérifier l'état de l'authentification
        console.log('[Docking] window.api existe:', !!window.api);
        if (window.api) {
            console.log('[Docking] isAuthenticated:', window.api.isAuthenticated());
        }

        if (!window.api || !window.api.isAuthenticated()) {
            select.innerHTML = '<option value="">Connectez-vous pour charger les analyses</option>';
            console.warn('[Docking] Non authentifié');
            return;
        }

        const data = await window.api.getHistory(0, 100);
        select.innerHTML = '<option value="">Sélectionnez une analyse...</option>';

        const analyses = (data && (data.analyses || data.data)) || [];
        let validCount = 0;

        analyses.forEach((analysis) => {
            const proteinSeq = extractProteinSequence(analysis);
            if (!proteinSeq || proteinSeq.length < 3) return;

            const option = document.createElement('option');
            option.value = String(analysis.id);
            option.dataset.proteinSequence = proteinSeq;
            option.textContent = `${analysis.name || 'Analyse ' + analysis.id} — ${proteinSeq.length} aa`;
            select.appendChild(option);
            validCount++;
        });

        if (validCount === 0) {
            select.innerHTML = '<option value="">Aucune analyse avec séquence protéique</option>';
            console.warn('[Docking] Aucune analyse protéique trouvée');
        } else {
            console.log(`[Docking] ${validCount} analyse(s) valides`);
        }
    } catch (error) {
        console.error('[Docking] Erreur chargement analyses:', error);
        const msg = (error && error.message) ? error.message : 'Erreur de connexion';
        if (select) {
            select.innerHTML = `<option value="">${msg.includes('Session') ? 'Session expirée — reconnectez-vous' : 'Erreur de connexion'}</option>`;
        }
        if (typeof showNotification === 'function') {
            showNotification(msg, 'error');
        }
    }
}

/**
 * Extrait la séquence protéique d'une analyse (formats v1/v2).
 */
function extractProteinSequence(analysis) {
    if (!analysis) return null;
    const data = analysis.data || {};

    if (data.protein_data) {
        if (typeof data.protein_data === 'string') return data.protein_data;
        if (data.protein_data.sequence) return data.protein_data.sequence;
        if (data.protein_data.protein_sequence) return data.protein_data.protein_sequence;
    }
    if (data.protein_sequence) return data.protein_sequence;
    if (data.sequence && /^[ACDEFGHIKLMNPQRSTVWY]+$/i.test(data.sequence) && data.sequence.length >= 3) {
        return data.sequence.toUpperCase();
    }
    return null;
}

function setupDockingEventListeners() {
    const select = document.getElementById('dockingAnalysisSelect');
    if (!select) return;

    select.addEventListener('change', function () {
        currentAnalysisId = this.value || null;
        const opt = this.options[this.selectedIndex];
        currentProteinSequence = opt && opt.dataset.proteinSequence
            ? opt.dataset.proteinSequence
            : null;
        console.log('[Docking] Analyse sélectionnée:', currentAnalysisId,
            'protéine:', currentProteinSequence ? currentProteinSequence.length + ' aa' : 'n/a');

        if (typeof validateLigandSelection === 'function') {
            validateLigandSelection();
        }
    });

    select.addEventListener('focus', function () {
        if (this.options.length <= 1) loadDockingAnalyses();
    });
}

/**
 * Lance AutoDock Vina (+ ASP Boltzmann post-traitement) via Celery.
 * Ancien version: api.php?action=start_docking (sync PHP)
 * Nouveau: POST /api/v1/docking/submit + poll status
 */
async function startDocking() {
    console.log('[Docking] startDocking — analysis:', currentAnalysisId);

    if (!currentAnalysisId) {
        showNotification('Veuillez sélectionner une analyse', 'warning');
        return;
    }
    if (dockingInProgress) {
        showNotification('Un docking est déjà en cours', 'warning');
        return;
    }

    const ligandSmiles = (document.getElementById('ligandSmiles')?.value || '').trim();
    if (!ligandSmiles) {
        showNotification('Veuillez sélectionner un ligand validé', 'warning');
        return;
    }

    const ligandSelect = document.getElementById('validatedLigandSelect');
    if (ligandSelect && !ligandSelect.value) {
        showNotification('Veuillez sélectionner un ligand dans la liste', 'warning');
        return;
    }

    if (!validateSmiles(ligandSmiles)) {
        showNotification('Format SMILES invalide', 'error');
        return;
    }

    if (!window.api || !window.api.isAuthenticated()) {
        showNotification('Session expirée — reconnectez-vous', 'error');
        return;
    }

    let proteinSequence = currentProteinSequence;
    if (!proteinSequence) {
        try {
            const detail = await window.api.getAnalysis(currentAnalysisId);
            proteinSequence = extractProteinSequence(detail);
        } catch (e) {
            console.error('[Docking] Impossible de récupérer l\'analyse:', e);
        }
    }

    if (!proteinSequence || proteinSequence.length < 3) {
        showNotification('Aucune séquence protéique dans cette analyse', 'error');
        displayDockingError('Séquence protéique manquante ou trop courte (min. 3 aa)');
        return;
    }

    dockingInProgress = true;
    showDockingProgress(true, 0, 'Soumission du job...');
    disableDockingControls(true);

    try {
        const result = await window.api.dockAndPoll(
            proteinSequence,
            ligandSmiles,
            function onProgress(status) {
                currentDockingJobId = status.job_id || currentDockingJobId;
                const pct = typeof status.progress === 'number' ? status.progress : 10;
                const message = status.status_message || '';
                const label = status.status === 'running'
                    ? message ? `${message} (${pct}%)` : `Docking Vina (+ ASP) en cours… ${pct}%`
                    : status.status === 'pending'
                        ? 'Job en file d\'attente…'
                        : `Statut: ${status.status}`;
                showDockingProgress(true, pct, label);
            },
            3000
        );

        // Normaliser le format de réponse pour l'UI
        const normalized = normalizeDockingResult(result);
        showNotification('Docking moléculaire réussi !', 'success');
        displayDockingResults(normalized);
        updateDockingHistory();

        const poseData = buildPoseData(normalized);
        if (poseData) {
            initDocking3DVisualization(poseData);
        }
    } catch (error) {
        console.error('[Docking] Erreur:', error);
        const msg = (error && error.message) ? error.message : 'Erreur de connexion au serveur';
        showNotification(msg, 'error');
        displayDockingError(msg);
    } finally {
        dockingInProgress = false;
        showDockingProgress(false);
        disableDockingControls(false);
    }
}

/** Alias debug -> même pipeline production */
async function startDockingDebug() {
    return startDocking();
}

function normalizeDockingResult(result) {
    if (!result) return { success: false, error: 'Résultat vide' };

    const bestPose = (result.poses && result.poses[0]) || (result.pose_data && result.pose_data.poses && result.pose_data.poses[0]) || null;
    const score = result.vina_best_score ?? result.docking_score ?? (bestPose && bestPose.score);
    const effectiveScore = result.boltzmann_effective_score ?? result.effective_score ?? null;
    const meanScore = result.boltzmann_mean_score ?? null;
    const poses = result.poses || (result.pose_data && result.pose_data.poses) || [];

    return {
        success: result.success !== false,
        docking_id: result.docking_id || result.job_id || currentDockingJobId,
        docking_score: score,
        boltzmann_mean_score: meanScore,
        boltzmann_effective_score: effectiveScore,
        effective_score: effectiveScore,
        partition_function: result.partition_function,
        execution_time: result.execution_time,
        docking_engine: result.docking_engine || 'AutoDock Vina',
        aggregation_method: result.aggregation_method || 'ASP — agrégation de scores par Boltzmann',
        modeling_method: result.modeling_method || null,
        poses: poses,
        num_poses: result.num_poses || (poses ? poses.length : 0),
        interactions: result.interactions || null,
        pose_data: result.pose_data || (bestPose ? { best_pose: bestPose, atoms: bestPose.atoms } : null),
        metadata: result.metadata || {},
        validation: result.validation || {},
        best_pocket: result.best_pocket || null,
        asp_metadata: result.aggregation_metadata || result.asp_metadata || null,
    };
}

function buildPoseData(results) {
    if (!results) return null;
    if (results.pose_data) {
        if (results.pose_data.atoms && results.pose_data.atoms.length) {
            return { atoms: results.pose_data.atoms.map(normalizeAtom) };
        }
        if (results.pose_data.poses && results.pose_data.poses[0] && results.pose_data.poses[0].atoms) {
            return { atoms: results.pose_data.poses[0].atoms.map(normalizeAtom) };
        }
    }
    if (results.poses && results.poses[0] && results.poses[0].atoms) {
        return { atoms: results.poses[0].atoms.map(normalizeAtom) };
    }
    return null;
}

function normalizeAtom(atom) {
    return {
        x: atom.x,
        y: atom.y,
        z: atom.z,
        element: (atom.element || atom.atom || 'C').toString().charAt(0).toUpperCase(),
        atom: atom.atom || atom.element || 'C',
        residue: atom.residue || atom.residue_name || 'LIG',
    };
}

function validateSmiles(smiles) {
    if (!smiles || smiles.length < 1) return false;
    // Caractères SMILES usuels (atomes, branches, cycles, stéréochimie)
    return /^[A-Za-z0-9@+\-\[\]\(\)=#$:\\\/\.\%]+$/.test(smiles);
}

function showDockingProgress(show, percent, label) {
    const progressDiv = document.getElementById('dockingProgress');
    const resultsDiv = document.getElementById('dockingResults');
    if (!progressDiv) return;

    if (show) {
        progressDiv.classList.remove('hidden');
        if (resultsDiv) resultsDiv.style.display = 'none';

        const fill = progressDiv.querySelector('.progress-fill');
        if (fill && typeof percent === 'number') {
            fill.style.width = Math.max(5, Math.min(100, percent)) + '%';
        }
        const span = progressDiv.querySelector('span');
        if (span && label) span.textContent = label;
    } else {
        progressDiv.classList.add('hidden');
        if (resultsDiv) resultsDiv.style.display = 'block';
    }
}

function disableDockingControls(disable) {
    const startBtn = document.getElementById('startDockingBtn');
    const select = document.getElementById('dockingAnalysisSelect');
    const ligandInput = document.getElementById('ligandSmiles');
    const ligandSelect = document.getElementById('validatedLigandSelect');

    if (startBtn) {
        startBtn.disabled = disable;
        startBtn.innerHTML = disable
            ? '<i class="fas fa-spinner fa-spin mr-2"></i>Calcul en cours...'
            : '<i class="fas fa-play mr-2"></i>Lancer l\'Amarrage';
    }
    if (select) select.disabled = disable;
    if (ligandInput) ligandInput.disabled = disable;
    if (ligandSelect) ligandSelect.disabled = disable;
}

function displayDockingResults(results) {
    const resultsDiv = document.getElementById('dockingResults');
    if (!resultsDiv) return;

    const score = results.docking_score;
    const effectiveScore = results.boltzmann_effective_score ?? results.effective_score ?? null;
    const interpretation = (results.validation && results.validation.score_interpretation)
        || (results.validation && results.validation.effective_score_interpretation)
        || '';
    const aspMetadata = results.aggregation_metadata || results.asp_metadata || {};
    const pocketResults = Array.isArray(aspMetadata.pocket_results) ? aspMetadata.pocket_results : [];

    const resultsHTML = `
        <div class="space-y-4">
            <div class="bg-green-900 bg-opacity-30 border border-green-400 p-4 rounded">
                <h4 class="font-bold text-green-400 mb-2">
                    <i class="fas fa-check-circle mr-2"></i>Docking Vina réussi (+ ASP)
                </h4>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <span class="text-sm text-gray-400">Score Vina (meilleure pose):</span>
                        <div class="text-2xl font-bold text-cyan-400">
                            ${fmtScore(score)} kcal/mol
                        </div>
                    </div>
                    <div>
                        <span class="text-sm text-gray-400">Score effectif pondéré (Boltzmann):</span>
                        <div class="text-2xl font-bold text-green-400">
                            ${fmtScore(effectiveScore)} <span class="text-sm font-normal">(échelle du score Vina)</span>
                        </div>
                    </div>
                    <div>
                        <span class="text-sm text-gray-400">Temps d'exécution:</span>
                        <div class="text-lg font-semibold text-yellow-400">
                            ${results.execution_time != null ? Number(results.execution_time).toFixed(2) : 'N/A'} s
                        </div>
                    </div>
                    <div>
                        <span class="text-sm text-gray-400">Méthode:</span>
                        <div class="text-lg font-semibold text-purple-400">
                            ${escapeHtmlText([results.docking_engine, results.aggregation_method].filter(Boolean).join(' + '))}
                        </div>
                    </div>
                </div>
                ${interpretation ? `<p class="mt-3 text-sm text-gray-300">${interpretation}</p>` : ''}
            </div>

            <div class="bg-gray-800 bg-opacity-50 p-4 rounded">
                <h4 class="font-bold text-blue-400 mb-2">
                    <i class="fas fa-info-circle mr-2"></i>Informations Scientifiques
                </h4>
                <ul class="text-sm space-y-1">
                    <li>• Analyse: ${currentAnalysisId || 'N/A'}</li>
                    <li>• Ligand SMILES: ${escapeHtmlText(document.getElementById('ligandSmiles')?.value || '')}</li>
                    <li>• Job ID: ${results.docking_id || currentDockingJobId || 'N/A'}</li>
                    <li>• Poses: ${results.num_poses || (results.poses ? results.poses.length : 0)}</li>
                    ${aspMetadata.rmsd_threshold_A != null ? `<li>• Clustering RMSD: ${aspMetadata.rmsd_threshold_A} Å</li>` : ''}
                    ${aspMetadata.num_pose_clusters != null ? `<li>• Représentants agrégés: ${aspMetadata.num_pose_clusters}</li>` : ''}
                    ${aspMetadata.best_pocket_id != null ? `<li>• Meilleure poche ASP: ${escapeHtmlText(String(aspMetadata.best_pocket_id))}</li>` : ''}
                    ${results.boltzmann_mean_score != null ? `<li>• Moyenne pondérée de Boltzmann: ${fmtScore(results.boltzmann_mean_score)} (échelle du score Vina)</li>` : ''}
                    <li>• Moteur: AutoDock Vina</li>
                    <li>• Agrégation: ASP — pondération/agrégation Boltzmann de scores de docking, après clustering RMSD</li>
                </ul>
            </div>

            ${results.poses && results.poses.length > 0 ? `
            <div class="bg-gray-800 bg-opacity-50 p-4 rounded">
                <h4 class="font-bold text-orange-400 mb-3">
                    <i class="fas fa-layer-group mr-2"></i>Poses (${results.poses.length})
                </h4>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 max-h-64 overflow-y-auto">
                    ${results.poses.slice(0, 12).map((pose, index) => `
                        <div class="bg-gray-700 p-3 rounded border ${index === 0 ? 'border-green-400' : 'border-gray-600'}">
                            <div class="flex justify-between items-center mb-2">
                                <span class="font-semibold ${index === 0 ? 'text-green-400' : 'text-gray-300'}">
                                    Pose ${(pose.pose_id != null ? pose.pose_id : index) + (typeof pose.pose_id === 'number' ? 0 : 1) || (index + 1)}
                                    ${index === 0 ? ' (Meilleure)' : ''}
                                </span>
                                <span class="text-xs text-cyan-400">
                                    ${fmtScore(pose.score)} kcal/mol
                                </span>
                            </div>
                            ${pose.pocket_id != null ? `<div class="text-xs text-gray-400 mb-1">Poche ${pose.pocket_id + 1}</div>` : ''}
                            <button type="button" onclick="visualizePose(${index})" class="w-full bg-blue-600 hover:bg-blue-700 text-white text-xs py-1 px-2 rounded">
                                <i class="fas fa-eye mr-1"></i>Voir 3D
                            </button>
                        </div>
                    `).join('')}
                </div>
            </div>
            ` : ''}

            ${results.interactions ? `
            <div class="bg-gray-800 bg-opacity-50 p-4 rounded">
                <h4 class="font-bold text-pink-400 mb-3">
                    <i class="fas fa-atom mr-2"></i>Interactions Moléculaires
                </h4>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div class="text-center">
                        <div class="text-2xl font-bold text-blue-400">${results.interactions.total_contacts || 0}</div>
                        <div class="text-sm text-gray-400">Contacts totaux</div>
                    </div>
                    <div class="text-center">
                        <div class="text-2xl font-bold text-green-400">${(results.interactions.hydrogen_bonds || []).length}</div>
                        <div class="text-sm text-gray-400">Liaisons hydrogène</div>
                    </div>
                    <div class="text-center">
                        <div class="text-2xl font-bold text-yellow-400">${(results.interactions.hydrophobic_contacts || []).length}</div>
                        <div class="text-sm text-gray-400">Contacts hydrophobes</div>
                    </div>
                </div>
            </div>
            ` : ''}
        </div>
    `;

    resultsDiv.innerHTML = resultsHTML;

    if (results.poses) {
        window.allPoses = results.poses.map((p) => ({
            ...p,
            atoms: (p.atoms || []).map(normalizeAtom),
        }));
    }
}

function fmtScore(v) {
    if (v == null || v === '' || Number.isNaN(Number(v))) return 'N/A';
    return Number(v).toFixed(3);
}

function escapeHtmlText(text) {
    const d = document.createElement('div');
    d.textContent = text == null ? '' : String(text);
    return d.innerHTML;
}

function visualizePose(poseIndex) {
    if (!window.allPoses || !window.allPoses[poseIndex]) {
        console.error('[Docking] Pose non disponible:', poseIndex);
        return;
    }
    const pose = window.allPoses[poseIndex];
    if (pose.atoms && typeof initDocking3DVisualization === 'function') {
        initDocking3DVisualization({ atoms: pose.atoms.map(normalizeAtom) });
    }
}

// Message informatif au cas ou le docking echoue
function displayDockingError(error) {
    const resultsDiv = document.getElementById('dockingResults');
    if (!resultsDiv) return;

    resultsDiv.innerHTML = `
        <div class="bg-red-900 bg-opacity-30 border border-red-400 p-4 rounded">
            <h4 class="font-bold text-red-400 mb-2">
                <i class="fas fa-exclamation-triangle mr-2"></i>Échec du Docking
            </h4>
            <p class="text-red-300">${escapeHtmlText(error)}</p>
            <div class="mt-4 text-sm text-gray-400">
                <p>Vérifiez:</p>
                <ul class="list-disc list-inside">
                    <li>Vous êtes connecté (token JWT valide)</li>
                    <li>La séquence protéique est valide (≥ 3 aa)</li>
                    <li>Le format SMILES du ligand est correct</li>
                    <li>Le worker Celery et AutoDock Vina sont démarrés</li>
                </ul>
            </div>
        </div>
    `;
}

async function updateDockingHistory() {
    try {
        if (!window.api || !window.api.isAuthenticated()) return;
        const history = await window.api.getDockingHistory();
        console.log('[Docking] Historique:', history);
        window.lastDockingHistory = history;
    } catch (error) {
        console.error('[Docking] Erreur historique:', error);
    }
}

function initDocking3DVisualization(poseData) {
    const viewer = document.getElementById('docking3DViewer');
    if (!viewer) {
        console.error('[Docking] Element docking3DViewer non trouvé');
        return;
    }

    if (!poseData || !poseData.atoms || !poseData.atoms.length) {
        viewer.innerHTML = `
            <div class="text-center text-gray-400">
                <i class="fas fa-cube text-4xl mb-4"></i>
                <p>Aucune donnée 3D disponible</p>
            </div>
        `;
        return;
    }

    if (typeof THREE === 'undefined') {
        viewer.innerHTML = '<div class="text-red-400 p-4">Three.js non disponible</div>';
        return;
    }

    try {
        const atoms = poseData.atoms.map(normalizeAtom);
        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0x1a1a2e);

        const camera = new THREE.PerspectiveCamera(
            75,
            Math.max(viewer.clientWidth, 1) / Math.max(viewer.clientHeight, 1),
            0.1,
            1000
        );
        camera.position.z = 30;

        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setSize(Math.max(viewer.clientWidth, 1), Math.max(viewer.clientHeight, 1));
        viewer.innerHTML = '';
        viewer.appendChild(renderer.domElement);

        const moleculeGroup = new THREE.Group();
        let controls = null;
        if (typeof THREE.OrbitControls !== 'undefined') {
            controls = new THREE.OrbitControls(camera, renderer.domElement);
            controls.enableDamping = true;
            controls.dampingFactor = 0.05;
        }

        const elementColors = {
            C: 0x404040, N: 0x3050f8, O: 0xff0d0d, S: 0xffff30,
            H: 0xffffff, P: 0xffa500, F: 0x90e050, Cl: 0x1ff01f,
        };

        atoms.forEach((atom) => {
            const geometry = new THREE.SphereGeometry(1.2, 24, 24);
            const el = (atom.element || 'C').toUpperCase();
            const material = new THREE.MeshPhongMaterial({
                color: elementColors[el] || elementColors[el.charAt(0)] || 0x808080,
            });
            const sphere = new THREE.Mesh(geometry, material);
            sphere.position.set(atom.x * 2, atom.y * 2, atom.z * 2);
            moleculeGroup.add(sphere);
        });

        // Liaisons entre atomes proches (< 2.0 Å)
        for (let i = 0; i < atoms.length; i++) {
            for (let j = i + 1; j < atoms.length; j++) {
                const a1 = atoms[i], a2 = atoms[j];
                const dx = a2.x - a1.x, dy = a2.y - a1.y, dz = a2.z - a1.z;
                const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
                if (distance > 0.4 && distance < 2.0) {
                    const bondGeometry = new THREE.CylinderGeometry(0.15, 0.15, distance * 2, 8);
                    const bondMaterial = new THREE.MeshPhongMaterial({ color: 0x888888 });
                    const bond = new THREE.Mesh(bondGeometry, bondMaterial);
                    bond.position.set((a1.x + a2.x), (a1.y + a2.y), (a1.z + a2.z));
                    const mid = new THREE.Vector3(a1.x * 2, a1.y * 2, a1.z * 2);
                    const end = new THREE.Vector3(a2.x * 2, a2.y * 2, a2.z * 2);
                    bond.position.copy(mid).add(end).multiplyScalar(0.5);
                    bond.lookAt(end);
                    bond.rotateX(Math.PI / 2);
                    moleculeGroup.add(bond);
                }
            }
        }

        scene.add(moleculeGroup);
        scene.add(new THREE.AmbientLight(0xffffff, 0.6));
        const dir = new THREE.DirectionalLight(0xffffff, 0.8);
        dir.position.set(1, 1, 1);
        scene.add(dir);

        // Centrer la caméra sur la molécule
        const box = new THREE.Box3().setFromObject(moleculeGroup);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z, 1);
        camera.position.set(center.x, center.y, center.z + maxDim * 1.8);
        if (controls) {
            controls.target.copy(center);
            controls.update();
        }

        let animId = null;
        function animate() {
            animId = requestAnimationFrame(animate);
            if (controls) controls.update();
            moleculeGroup.rotation.y += 0.003;
            renderer.render(scene, camera);
        }
        animate();

        // Nettoyer l'ancienne animation si re-render
        if (viewer._dockingAnimId) cancelAnimationFrame(viewer._dockingAnimId);
        viewer._dockingAnimId = animId;

        window.addEventListener('resize', function onResize() {
            if (!viewer.contains(renderer.domElement)) {
                window.removeEventListener('resize', onResize);
                return;
            }
            camera.aspect = Math.max(viewer.clientWidth, 1) / Math.max(viewer.clientHeight, 1);
            camera.updateProjectionMatrix();
            renderer.setSize(Math.max(viewer.clientWidth, 1), Math.max(viewer.clientHeight, 1));
        });
    } catch (error) {
        console.error('[Docking] Erreur 3D:', error);
        viewer.innerHTML = `<div class="text-red-400 p-4">Erreur 3D: ${escapeHtmlText(error.message)}</div>`;
    }
}

function clearDockingResults() {
    const resultsDiv = document.getElementById('dockingResults');
    const viewer = document.getElementById('docking3DViewer');

    if (resultsDiv) {
        resultsDiv.innerHTML = `
            <div class="text-center text-gray-400 mt-20">
                <i class="fas fa-atom text-4xl mb-4"></i>
                <p>Aucun résultat de docking</p>
                <p class="text-sm mt-2">Lancez un amarrage pour voir les résultats</p>
            </div>
        `;
    }
    if (viewer) {
        viewer.innerHTML = `
            <div class="text-center text-gray-400">
                <i class="fas fa-cube text-4xl mb-4"></i>
                <p>Visualisation 3D disponible après docking</p>
            </div>
        `;
    }
    window.allPoses = null;
    if (typeof showNotification === 'function') {
        showNotification('Résultats effacés', 'info');
    }
}

function showNotification(message, type) {
    // Déléguer au système global s'il existe une version plus riche
    if (window.nexoraApp && typeof window.nexoraApp.emit === 'function') {
        window.nexoraApp.emit('notification:show', { message, type, duration: 4000 });
    }

    const notification = document.createElement('div');
    notification.className = `fixed top-4 right-4 p-4 rounded-lg shadow-lg z-50 ${
        type === 'success' ? 'bg-green-600' :
        type === 'error' ? 'bg-red-600' :
        type === 'warning' ? 'bg-yellow-600' :
        'bg-blue-600'
    } text-white max-w-sm`;
    notification.textContent = message;
    document.body.appendChild(notification);
    setTimeout(() => {
        if (notification.parentNode) notification.parentNode.removeChild(notification);
    }, 3500);
}

function onDockingTabShow() {
    console.log('[Docking] Onglet affiché');
    initDockingModule();
    if (typeof initLigandsModule === 'function') {
        initLigandsModule();
    }
}

document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('docking')) {
        initDockingModule();
    }
});

window.startDocking = startDocking;
window.startDockingDebug = startDockingDebug;
window.clearDockingResults = clearDockingResults;
window.initDockingModule = initDockingModule;
window.loadDockingAnalyses = loadDockingAnalyses;
window.onDockingTabShow = onDockingTabShow;
window.visualizePose = visualizePose;
window.initDocking3DVisualization = initDocking3DVisualization;
window.displayDockingResults = displayDockingResults;
