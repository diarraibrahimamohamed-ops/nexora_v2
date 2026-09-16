// async-optimizer.js
// Sous le seuil : traitement navigateur par chunks.
// Au-delà : délégation serveur Celery (POST /analysis/sequence → poll status).
(function () {
    "use strict";

    const CHUNK_SIZE = 20000;
    const PAUSE_MS = 3;
    const LONG_PAUSE_MS = 5;
    const THRESHOLD = 100000; // Aligné sur BROWSER_SEQUENCE_THRESHOLD backend

    const originalFunctions = {};

    async function processInChunks(sequence, processor) {
        let processedChunks = 0;
        for (let i = 0; i < sequence.length; i += CHUNK_SIZE) {
            const chunk = sequence.slice(i, i + CHUNK_SIZE);
            await processor(chunk, i);
            processedChunks++;
            if (processedChunks % 5 === 0 && window.gc) {
                window.gc();
                await new Promise((r) => setTimeout(r, LONG_PAUSE_MS));
            } else {
                await new Promise((r) => setTimeout(r, PAUSE_MS));
            }
        }
    }

    function authHeaders() {
        const token =
            localStorage.getItem("token") || sessionStorage.getItem("token");
        const h = { "Content-Type": "application/json" };
        if (token) h.Authorization = `Bearer ${token}`;
        return h;
    }

    /** Analyse lourde côté serveur (Celery file analysis). */
    async function analyzeOnServerAsync(sequence) {
        const submit = await fetch("/api/v1/analysis/sequence", {
            method: "POST",
            headers: authHeaders(),
            body: JSON.stringify({ sequence, force_async: true }),
        });
        if (!submit.ok) {
            throw new Error(`Soumission serveur échouée (${submit.status})`);
        }
        const data = await submit.json();
        if (!data.async) {
            return data; // sync fallback
        }
        const id = data.analysis_id;
        for (let i = 0; i < 600; i++) {
            await new Promise((r) => setTimeout(r, 2000));
            const st = await fetch(`/api/v1/analysis/sequence/${id}/status`, {
                headers: authHeaders(),
            });
            if (!st.ok) throw new Error(`Statut serveur ${st.status}`);
            const body = await st.json();
            if (typeof updateProgressStep === "function") {
                updateProgressStep(
                    2,
                    "running",
                    `Serveur async… ${body.status} (${body.input_length || "?"} nt)`
                );
            }
            if (body.status === "completed") return body.result || body;
            if (body.status === "failed") {
                throw new Error(body.error || "Analyse serveur échouée");
            }
        }
        throw new Error("Timeout analyse serveur");
    }

    // Expose for FASTA / analyzeSequence
    window.nexoraAnalyzeLargeOnServer = analyzeOnServerAsync;
    window.NEXORA_BROWSER_SEQ_THRESHOLD = THRESHOLD;

    async function validateSequenceAsync(sequence) {
        try {
            await originalFunctions.validateSequence(sequence);
            updateProgressStep(1, "completed");
        } catch (e) {
            updateProgressStep(1, "error", e.message);
            throw e;
        }
    }

    async function analyzeNucleotidesAsync(sequence) {
        if (sequence.length > THRESHOLD) {
            const serverResult = await analyzeOnServerAsync(sequence);
            updateProgressStep(2, "completed", "Analyse nucléotidique (serveur)");
            return serverResult;
        }
        let totalCounts = { A: 0, T: 0, C: 0, G: 0 };
        await processInChunks(sequence, async (chunk, offset) => {
            const chunkCounts = originalFunctions.analyzeNucleotides(chunk);
            if (chunkCounts) {
                for (const base in chunkCounts) {
                    totalCounts[base] += chunkCounts[base] || 0;
                }
            }
            updateProgressStep(
                2,
                "running",
                `Analyse nucléotidique (${Math.min(offset + chunk.length, sequence.length)}/${sequence.length})`
            );
        });
        const total = Object.values(totalCounts).reduce((a, b) => a + b, 0);
        const percentages = {};
        for (const base in totalCounts) {
            percentages[base] = ((totalCounts[base] / total) * 100).toFixed(2);
        }
        updateProgressStep(2, "completed", "Analyse nucléotidique terminée");
        return { counts: totalCounts, percentages };
    }

    async function detectMutationsAsync(sequence, reference) {
        const minLength = Math.min(sequence.length, reference.length);
        await processInChunks(sequence.substring(0, minLength), async (chunk, offset) => {
            const refChunk = reference.substring(offset, offset + chunk.length);
            originalFunctions.detectMutations(chunk, refChunk);
            updateProgressStep(
                3,
                "running",
                `Détection mutations (${Math.min(offset + chunk.length, minLength)}/${minLength})`
            );
        });
        updateProgressStep(3, "completed");
    }

    async function transcribeAndTranslateAsync(sequence) {
        if (sequence.length > THRESHOLD) {
            // Traduction complète déjà couverte côté serveur via fragments
            updateProgressStep(4, "completed", "Traduction déléguée (serveur)");
            return null;
        }
        let remainder = "";
        let fullRna = "";
        await processInChunks(sequence, async (chunk) => {
            const fullChunk = remainder + chunk;
            const rna = fullChunk.replace(/T/g, "U");
            remainder = rna.slice(-(rna.length % 3));
            const translatable = rna.slice(0, rna.length - remainder.length);
            fullRna += translatable;
        });
        if (remainder.length >= 3) {
            fullRna += remainder.slice(0, remainder.length - (remainder.length % 3));
        }
        const protein = originalFunctions.transcribeAndTranslate(fullRna);
        updateProgressStep(4, "completed", "Traduction terminée");
        return protein;
    }

    async function analyzeResistanceProfileAsync() {
        updateProgressStep(5, "running", "Analyse de résistance...");
        await new Promise((r) => setTimeout(r, 500));
        originalFunctions.analyzeResistanceProfile();
        updateProgressStep(5, "completed");
    }

    async function update3DModelsAsync() {
        updateProgressStep(6, "running", "Mise à jour modèles 3D...");
        await new Promise((r) => setTimeout(r, 500));
        originalFunctions.update3DModels();
        updateProgressStep(6, "completed");
    }

    async function updateCountersAsync() {
        updateProgressStep(7, "running", "Mise à jour compteurs...");
        await new Promise((r) => setTimeout(r, 200));
        originalFunctions.updateCounters();
        updateProgressStep(7, "completed");
    }

    function overrideFunction(name, asyncVersion) {
        if (typeof window[name] === "function") {
            originalFunctions[name] = window[name];
            window[name] = function (...args) {
                const largeInput = args.find(
                    (a) => typeof a === "string" && a.length > THRESHOLD
                );
                if (largeInput) {
                    console.log(
                        `Mode async serveur pour ${name} (${largeInput.length.toLocaleString()} caractères)`
                    );
                    return asyncVersion.apply(this, args);
                }
                return originalFunctions[name].apply(this, args);
            };
        }
    }

    function initializeAsyncOptimizer() {
        console.log("Initialisation Async Optimizer (seuil navigateur → serveur)...");
        overrideFunction("validateSequence", validateSequenceAsync);
        overrideFunction("analyzeNucleotides", analyzeNucleotidesAsync);
        overrideFunction("detectMutations", detectMutationsAsync);
        overrideFunction("transcribeAndTranslate", transcribeAndTranslateAsync);
        overrideFunction("analyzeResistanceProfile", analyzeResistanceProfileAsync);
        overrideFunction("update3DModels", update3DModelsAsync);
        overrideFunction("updateCounters", updateCountersAsync);
        console.log(`Async Optimizer prêt (seuil: ${THRESHOLD.toLocaleString()} bp → Celery)`);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () =>
            setTimeout(initializeAsyncOptimizer, 100)
        );
    } else {
        setTimeout(initializeAsyncOptimizer, 100);
    }
})();
