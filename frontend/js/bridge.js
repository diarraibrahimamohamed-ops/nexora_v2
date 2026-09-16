/**
 * Pont de compatibilité entre anciens et nouveaux modules
 * Rend les fonctions nécessaires disponibles globalement
 */

(function() {
    'use strict';

    console.log(' Initialisation du pont de compatibilité...');

    // Attendre que le DOM soit chargé
    document.addEventListener('DOMContentLoaded', function() {
        // Délai pour laisser les modules s'initialiser
        setTimeout(function() {
            setupGlobalFunctions();
            console.log(' Pont de compatibilité établi');
        }, 200);
    });

    function setupGlobalFunctions() {
        // Configuration minimale sécurisée en attendant les modules complets
        if (typeof CONFIG !== 'undefined' && CONFIG.API) {
            window.API_CONFIG = CONFIG.API;
        }

        // Variables globales définies indépendamment pour éviter les dépendances circulaires
        window.currentSequence = window.currentSequence || '';
        window.currentReference = window.currentReference || '';
        window.currentRNA = window.currentRNA || '';
        window.currentProtein = window.currentProtein || '';
        window.mutations = window.mutations || [];
        window.analysisHistory = window.analysisHistory || [];
        window.currentUser = window.currentUser || null;
        window.currentFastaFile = window.currentFastaFile || null;
        window.currentFastaSequences = window.currentFastaSequences || [];
        window.ncbiSearchHistory = window.ncbiSearchHistory || [];

        // Synchronisation sécurisée avec le state manager
        try {
            if (typeof state !== 'undefined' && typeof setState === 'function') {
                setState('currentSequence', window.currentSequence);
                setState('currentReference', window.currentReference);
                setState('mutations', window.mutations);
                setState('analysisHistory', window.analysisHistory);
            }
        } catch (e) {
            console.warn('Gestion d\'état non-disponible:', e);
        }

        // Fonctions de compatibilité définit indépendamment pour éviter les erreurs de référence
        window.updateProgressStep = function(step, status, message = '') {
            if (nexoraApp && typeof nexoraApp.emit === 'function') {
                nexoraApp.emit('progress:update', { step, status, message });
            } else {
                console.log(`[${step}] ${status}: ${message}`);
            }
        };

        window.showNotification = function(message, type = 'info', duration = 5000) {
            if (nexoraApp && typeof nexoraApp.emit === 'function') {
                nexoraApp.emit('notification:show', { message, type, duration });
            }
            // Affichage basique en attendant
            console.log(`[${type.toUpperCase()}] ${message}`);
        };

        window.showTab = function(tabName) {
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            document.querySelectorAll('.tab').forEach(tab => {
                tab.classList.remove('active');
            });

            const targetContent = document.getElementById(tabName);
            if (targetContent) {
                targetContent.classList.add('active');
            }

            const evt = window.event;
            if (evt && evt.currentTarget) {
                evt.currentTarget.classList.add('active');
            } else {
                const tabs = document.querySelectorAll('.tab');
                tabs.forEach(tab => {
                    if (tab.getAttribute('onclick') && tab.getAttribute('onclick').includes(`'${tabName}'`)) {
                        tab.classList.add('active');
                    }
                });
            }

            if (tabName === 'docking' && typeof window.onDockingTabShow === 'function') {
                window.onDockingTabShow();
            }
        };

        // Gestion sécurisée des fonctions API
        try {
            if (typeof api !== 'undefined' && api !== null) {
                window.api = api;
            }
        } catch (e) {
            console.warn('Impossible de définir window.api:', e);
        }

        // Ne pas promouvoir un jeton de session vers localStorage :
        // cela annulerait le choix « Se souvenir de moi ».

        // Variables pour les 3D
        window.dnaScene = null;
        window.dnaCamera = null;
        window.dnaRenderer = null;
        window.proteinScene = null;
        window.proteinCamera = null;
        window.proteinRenderer = null;

        // Charts
        window.nucleotideChart = null;
        window.rnaChart = null;
        window.resistanceChart = null;
        window.aminoAcidChart = null;
        window.complexityChart = null;
        window.ncbiStatsChart = null;

        // Cache
        window.ncbiCache = new Map();
        window.aiInterpretations = {};

        console.log(' Toutes les fonctions globales sont disponibles');
    }

    // Exposer globalement pour debug
    window.bridgeDebug = function() {
        return {
            CONFIG: typeof CONFIG !== 'undefined',
            state: typeof state !== 'undefined',
            api: typeof api !== 'undefined',
            nexoraApp: typeof nexoraApp !== 'undefined',
            globals: {
                API_CONFIG: typeof window.API_CONFIG !== 'undefined',
                currentSequence: typeof window.currentSequence !== 'undefined',
                updateProgressStep: typeof window.updateProgressStep === 'function'
            }
        };
    };

})();
