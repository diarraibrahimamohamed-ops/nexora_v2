/**
 * frontend/js/api.js — Client HTTP centralisé Nexora v2
 * Tous les appels API passent ici. Zéro appel direct vers PHP.
 *
 * Import dans les autres modules :
 *   import { api } from './api.js';
 */

const API_BASE = '/api/v1';

class NexoraAPI {
    constructor() {
        this._token = localStorage.getItem('nexora_token') || localStorage.getItem('token') || sessionStorage.getItem('token') || sessionStorage.getItem('authToken');
    }

    setToken(token, remember = false) {
        this._token = token;
        localStorage.removeItem('token');
        localStorage.removeItem('nexora_token');
        sessionStorage.removeItem('token');
        sessionStorage.removeItem('nexora_token');
        if (remember) {
            localStorage.setItem('token', token);
            localStorage.setItem('nexora_token', token);
        } else {
            sessionStorage.setItem('token', token);
            sessionStorage.setItem('nexora_token', token);
        }
    }
    clearToken() {
        this._token = null;
        localStorage.removeItem('token');
        localStorage.removeItem('nexora_token');
        localStorage.removeItem('authToken');
        sessionStorage.removeItem('token');
        sessionStorage.removeItem('nexora_token');
        sessionStorage.removeItem('authToken');
    }
    isAuthenticated() { return !!this._token; }

    _headers(isJson = true) {
        const h = {};
        if (this._token) h['Authorization'] = `Bearer ${this._token}`;
        if (isJson)      h['Content-Type']   = 'application/json';
        return h;
    }

    async _req(method, path, body = null, isForm = false) {
        const opts = { method, headers: this._headers(!isForm) };
        if (body) opts.body = isForm ? body : JSON.stringify(body);
        const r = await fetch(`${API_BASE}${path}`, opts);
        if (r.status === 401) { this.clearToken(); throw new Error('Session expirée — reconnectez-vous'); }
        if (!r.ok) {
            const err = await r.json().catch(() => ({ detail: r.statusText }));
            throw new Error(err.detail || 'Erreur serveur');
        }
        return r.json();
    }

    get(path)              { return this._req('GET',    path); }
    post(path, body)       { return this._req('POST',   path, body); }
    patch(path, body)      { return this._req('PATCH',  path, body); }
    del(path)              { return this._req('DELETE', path); }

    // verifcation de l'authentification
    async login(email, password, remember = false) {
        const r = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password, remember }),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ detail: 'Login échoué' }));
            throw new Error(err.detail || 'Email ou mot de passe incorrect');
        }
        const data = await r.json();
        this.setToken(data.access_token, remember);
        return data;
    }
    register(username, email, password) { return this.post('/auth/register', { username, email, password }); }
    me()                                { return this.get('/auth/me'); }

    // Analyse séquences (remplace s.php de l'ancienne version) 
    analyzeSequence(sequence) { return this.post('/analysis/sequence', { sequence }); }
    afribioAlign(data)        { return this.post('/analysis/afribio/align', data); }
    proteinProperties(sequence) { return this.post('/analysis/protein-properties', { sequence }); }
    afribioPhylo(data)        { return this.post('/analysis/afribio/phylo', data); }
    saveAnalysis(data)        { return this.post('/analysis/save', data); }
    getHistory(skip=0, limit=20) { return this.get(`/analysis/history?skip=${skip}&limit=${limit}`); }
    getStats()                { return this.get('/analysis/stats'); }
    getAnalysis(id)           { return this.get(`/analysis/${id}`); }
    deleteAnalysis(id)        { return this.del(`/analysis/${id}`); }

    // Recherche NCBI (remplace api.php?action=search_ncbi/fetch_ncbi_sequence de l'ancienne version) 
    searchNCBI(query, db='nucleotide', max=10) {
        return this.get(`/sequences/ncbi?query=${encodeURIComponent(query)}&database=${db}&max_results=${max}`);
    }
    fetchNCBISequence(accession, db='nucleotide') {
        return this.get(`/sequences/ncbi/fetch?accession=${encodeURIComponent(accession)}&database=${db}`);
    }
    translateDNA(dna_sequence) { return this.post('/sequences/translate', { dna_sequence }); }

// telechargement du fichier fasta
    async uploadFasta(name, file) {
        const form = new FormData();
        form.append('name', name);
        form.append('file', file);
        return this._req('POST', '/sequences/upload', form, true);
    }

    // Soumission du processus de Docking et obtention des resultats (remplace api.php?action=start_docking de l'ancienne version)
    submitDocking(protein_sequence, ligand_smiles, max_pockets=3) {
        return this.post('/docking/submit', { protein_sequence, ligand_smiles, max_pockets });
    }
    getDockingStatus(job_id)  { return this.get(`/docking/${job_id}/status`); }
    getDockingResults(job_id) { return this.get(`/docking/${job_id}/results`); }
    getDockingHistory()       { return this.get('/docking/history'); }

    async dockAndPoll(protein_sequence, ligand_smiles, onProgress, intervalMs=3000) {
        const { job_id } = await this.submitDocking(protein_sequence, ligand_smiles);
        const startTime = Date.now();
        const MAX_TIMEOUT_MS = 45 * 60 * 1000; // 45 minutes
        let highProgressCount = 0;

        return new Promise((resolve, reject) => {
            const iv = setInterval(async () => {
                try {
                    if (Date.now() - startTime > MAX_TIMEOUT_MS) {
                        clearInterval(iv);
                        reject(new Error('Temps d\'attente dépassé (45 min) — vérifiez l\'historique de vos calculs.'));
                        return;
                    }

                    const s = await this.getDockingStatus(job_id);
                    if (onProgress) onProgress(s);

                    if (s.status === 'completed' || s.status === 'done') {
                        clearInterval(iv);
                        resolve(await this.getDockingResults(job_id));
                        return;
                    } else if (s.status === 'failed') {
                        clearInterval(iv);
                        reject(new Error(s.error || 'Job échoué'));
                        return;
                    }

                    // Garde-fou anti-stagnation : Si le statut reste bloqué à progress >= 95% pendant 3 cycles (9s)
                    if (s.progress >= 95) {
                        highProgressCount++;
                        if (highProgressCount >= 3) {
                            try {
                                const results = await this.getDockingResults(job_id);
                                if (results && (results.docking_score != null || results.binding_energy != null || (results.poses && results.poses.length > 0))) {
                                    console.warn(`[dockAndPoll] Garde-fou déclenché : progress=${s.progress}% depuis 3 cycles, résultats récupérés directement.`);
                                    clearInterval(iv);
                                    resolve(results);
                                    return;
                                }
                            } catch (fallbackErr) {
                                // Fichier de résultats pas encore prêt ou lisible, on continue à poller
                            }
                        }
                    } else {
                        highProgressCount = 0;
                    }
                } catch(e) {
                    clearInterval(iv);
                    reject(e);
                }
            }, intervalMs);
        });
    }

    // paramettrage de la validation des Ligands et de leur categorie (remplace ligands_api.php de l'ancienne version )
    getLigands(params={})        { return this.get(`/ligands?${new URLSearchParams(params)}`); }
    getLigandCategories()        { return this.get('/ligands/categories'); }
    searchLigands(q, limit=20)   { return this.get(`/ligands/search?q=${encodeURIComponent(q)}&limit=${limit}`); }
    getLigand(id)                { return this.get(`/ligands/${id}`); }
    validateLigand(id, analysis_id) { return this.post(`/ligands/${id}/validate?analysis_id=${analysis_id}`, {}); }

    // Recherche scientifique / N3XORA-Sahel
    createResearchStudy(data) { return this.post('/research/studies', data); }
    listResearchStudies(skip=0, limit=20) { return this.get(`/research/studies?skip=${skip}&limit=${limit}`); }
    getResearchStudy(id) { return this.get(`/research/studies/${id}`); }
    addResearchVariant(studyId, data) { return this.post(`/research/studies/${studyId}/variants`, data); }
    annotateResearchNucleotideVariant(studyId, variantId, data) { return this.post(`/research/studies/${studyId}/variants/${variantId}/nucleotide-consequence`, data); }
    annotateResearchVariant(studyId, variantId, data) { return this.post(`/research/studies/${studyId}/variants/${variantId}/protein-consequence`, data); }
    materializeResearchVariantTarget(studyId, variantId, data) { return this.post(`/research/studies/${studyId}/variants/${variantId}/materialize-target`, data); }
    importResearchVariantsFromAnalysis(studyId, analysisId) { return this.post(`/research/studies/${studyId}/variants/from-analysis/${analysisId}`, {}); }
    addResearchTarget(studyId, data) { return this.post(`/research/studies/${studyId}/targets`, data); }
    importResearchTargetFromAnalysis(studyId, analysisId) { return this.post(`/research/studies/${studyId}/targets/from-analysis/${analysisId}`, {}); }
    startResearchDocking(studyId, data) { return this.post(`/research/studies/${studyId}/docking`, data); }
    listResearchDockingRuns(studyId) { return this.get(`/research/studies/${studyId}/docking-runs`); }
    startResearchDockingComparison(studyId, data) { return this.post(`/research/studies/${studyId}/docking-comparisons`, data); }
    listResearchDockingComparisons(studyId) { return this.get(`/research/studies/${studyId}/docking-comparisons`); }
    getResearchDockingComparison(studyId, comparisonId) { return this.get(`/research/studies/${studyId}/docking-comparisons/${comparisonId}`); }
    prepareResearchScreening(studyId, data) { return this.post(`/research/studies/${studyId}/screening/prepare`, data); }
    listResearchScreenings(studyId) { return this.get(`/research/studies/${studyId}/screening`); }
    getResearchScreening(studyId, screeningId) { return this.get(`/research/studies/${studyId}/screening/${screeningId}`); }
    launchResearchScreening(studyId, screeningId) { return this.post(`/research/studies/${studyId}/screening/${screeningId}/launch`, {}); }
    getResearchScreeningStatus(studyId, screeningId) { return this.get(`/research/studies/${studyId}/screening/${screeningId}/status`); }
    resolveResearchStructure(studyId, targetId, provider='auto_rcsb') { return this.post(`/research/studies/${studyId}/targets/${targetId}/resolve-structure`, { provider }); }
    listResearchStructureJobs(studyId) { return this.get(`/research/studies/${studyId}/structure-jobs`); }
    compareResearchStructures(studyId, data) { return this.post(`/research/studies/${studyId}/structure-comparisons`, data); }
    listResearchStructureComparisons(studyId) { return this.get(`/research/studies/${studyId}/structure-comparisons`); }
    async uploadResearchStructure(studyId, targetId, file) { const form=new FormData(); form.append('file', file); return this._req('POST', `/research/studies/${studyId}/targets/${targetId}/structure/upload`, form, true); }
    async getResearchStructureText(studyId, targetId) {
        const r=await fetch(`${API_BASE}/research/studies/${studyId}/targets/${targetId}/structure`, {headers:this._headers(false)});
        if(r.status===401){this.clearToken(); throw new Error('Session expirée — reconnectez-vous');}
        if(!r.ok){const err=await r.json().catch(()=>({detail:r.statusText})); throw new Error(err.detail||'Structure indisponible');}
        return r.text();
    }

    adminOverview() { return this.get('/admin/overview'); }
    adminUsers(q = '', limit = 50) {
        const p = new URLSearchParams({ limit });
        if (q) p.set('q', q);
        return this.get(`/admin/users?${p}`);
    }
    adminSetUserStatus(id, status) { return this.patch(`/admin/users/${id}/status`, { status }); }
    adminSetUserAdmin(id, is_admin) { return this.patch(`/admin/users/${id}/admin`, { is_admin }); }
    adminAnalyses(status = '', limit = 100) {
        const p = new URLSearchParams({ limit });
        if (status) p.set('status', status);
        return this.get(`/admin/analyses?${p}`);
    }
    adminDeleteAnalysis(id) { return this.del(`/admin/analyses/${id}`); }
    adminJobs(limit = 100) { return this.get(`/admin/jobs?limit=${limit}`); }
    adminRevokeJob(kind, id) { return this.post(`/admin/jobs/${kind}/${id}/revoke`, {}); }
}

// Instance globale - compatible avec les scripts non-modulaires
const api = new NexoraAPI();

// Compatibilité globale (pour code non-modulaire)
window.api = api;
window.nexoraAPI = api;
