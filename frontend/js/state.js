/**
 * Gestionnaire d'état centralisé pour Nexora
 * Pattern State Management avec observers
 */

class StateManager {
    constructor() {
        this.state = {
            // Authentification
            currentUser: null,
            isAuthenticated: false,

            // Séquences et analyses
            currentSequence: '',
            currentReference: '',
            currentRNA: '',
            currentProtein: '',
            mutations: [],
            analysisHistory: [],

            // NCBI et données externes
            ncbiSearchHistory: [],
            ncbiCache: new Map(),
            currentFastaFile: null,
            currentFastaSequences: [],

            // Visualisation 3D
            dnaModel: null,
            proteinModel: null,

            // Graphiques et métriques
            nucleotideChart: null,
            rnaChart: null,
            resistanceChart: null,
            aminoAcidChart: null,
            complexityChart: null,
            ncbiStatsChart: null,

            // UI State
            loadingStates: new Map(),
            progressSteps: new Map(),
            notifications: []
        };

        this.listeners = new Map();
        this.history = [];
        this.maxHistorySize = 10;
    }

    /**
     * Récupère une valeur d'état
     */
    get(key, defaultValue = null) {
        const keys = key.split('.');
        let value = this.state;

        for (const k of keys) {
            if (value && typeof value === 'object' && k in value) {
                value = value[k];
            } else {
                return defaultValue;
            }
        }

        return value;
    }

    /**
     * Définit une valeur d'état
     */
    set(key, value, options = {}) {
        const keys = key.split('.');
        const lastKey = keys.pop();
        let obj = this.state;

        // Créer les objets intermédiaires si nécessaire
        for (const k of keys) {
            if (!(k in obj) || typeof obj[k] !== 'object') {
                obj[k] = {};
            }
            obj = obj[k];
        }

        // Sauvegarder l'ancienne valeur pour l'historique
        const oldValue = obj[lastKey];

        // Mettre à jour la valeur
        obj[lastKey] = value;

        // Ajouter à l'historique si demandé
        if (options.history !== false) {
            this.addToHistory(key, oldValue, value);
        }

        // Notifier les listeners
        this.notify(key, value, oldValue);

        // Persister si demandé
        if (options.persist) {
            this.persist(key, value);
        }
    }

    /**
     * Met à jour plusieurs valeurs d'état
     */
    update(updates, options = {}) {
        for (const [key, value] of Object.entries(updates)) {
            this.set(key, value, { ...options, batch: true });
        }

        // Notification groupée pour les updates batch
        if (options.batch) {
            this.notify('batch_update', updates);
        }
    }

    /**
     * Souscrit aux changements d'état
     */
    subscribe(key, callback, options = {}) {
        if (!this.listeners.has(key)) {
            this.listeners.set(key, []);
        }

        this.listeners.get(key).push({
            callback,
            once: options.once || false,
            context: options.context || null
        });

        // Retourner une fonction de désabonnement
        return () => this.unsubscribe(key, callback);
    }

    /**
     * Se désabonne des changements d'état
     */
    unsubscribe(key, callback) {
        if (!this.listeners.has(key)) return;

        const listeners = this.listeners.get(key);
        const index = listeners.findIndex(l => l.callback === callback);

        if (index > -1) {
            listeners.splice(index, 1);
        }

        if (listeners.length === 0) {
            this.listeners.delete(key);
        }
    }

    /**
     * Notifie les listeners d'un changement
     */
    notify(key, newValue, oldValue) {
        if (!this.listeners.has(key)) return;

        const listeners = this.listeners.get(key);
        const toRemove = [];

        listeners.forEach((listener, index) => {
            try {
                listener.callback.call(
                    listener.context,
                    newValue,
                    oldValue,
                    key
                );

                if (listener.once) {
                    toRemove.push(index);
                }
            } catch (error) {
                console.error('Erreur dans listener pour', key, error);
            }
        });

        // Supprimer les listeners "once"
        toRemove.reverse().forEach(index => {
            listeners.splice(index, 1);
        });
    }

    /**
     * Ajoute une action à l'historique
     */
    addToHistory(key, oldValue, newValue) {
        this.history.push({
            timestamp: Date.now(),
            key,
            oldValue,
            newValue
        });

        if (this.history.length > this.maxHistorySize) {
            this.history.shift();
        }
    }

    /**
     * Annule la dernière action
     */
    undo() {
        const lastAction = this.history.pop();
        if (lastAction) {
            this.set(lastAction.key, lastAction.oldValue, { history: false });
            return lastAction;
        }
        return null;
    }

    /**
     * Persiste une valeur dans localStorage
     */
    persist(key, value) {
        try {
            const serialized = JSON.stringify(value);
            localStorage.setItem(`nexora_${key}`, serialized);
        } catch (error) {
            console.warn('Erreur de persistance pour', key, error);
        }
    }

    /**
     * Restaure une valeur depuis localStorage
     */
    restore(key) {
        try {
            const serialized = localStorage.getItem(`nexora_${key}`);
            if (serialized) {
                const value = JSON.parse(serialized);
                this.set(key, value, { history: false });
                return value;
            }
        } catch (error) {
            console.warn('Erreur de restauration pour', key, error);
        }
        return null;
    }

    /**
     * Réinitialise l'état
     */
    reset(options = {}) {
        const keysToReset = options.keys || Object.keys(this.state);

        keysToReset.forEach(key => {
            if (key in this.state) {
                const defaultValue = this.getDefaultValue(key);
                this.set(key, defaultValue, { history: false });
            }
        });

        if (options.clearHistory) {
            this.history = [];
        }

        if (options.clearStorage) {
            Object.keys(localStorage)
                .filter(key => key.startsWith('nexora_'))
                .forEach(key => localStorage.removeItem(key));
        }
    }

    /**
     * Obtient la valeur par défaut pour une clé
     */
    getDefaultValue(key) {
        const defaults = {
            currentUser: null,
            isAuthenticated: false,
            currentSequence: '',
            currentReference: '',
            mutations: [],
            analysisHistory: [],
            ncbiSearchHistory: [],
            notifications: []
        };

        return defaults[key] || (Array.isArray(this.state[key]) ? [] : null);
    }

    /**
     * Exporte l'état actuel
     */
    export() {
        return JSON.parse(JSON.stringify(this.state));
    }

    /**
     * Importe un état
     */
    import(stateData) {
        this.state = { ...this.state, ...stateData };
        this.notify('state_imported', this.state);
    }

    /**
     * Statistiques de l'état
     */
    getStats() {
        return {
            listenersCount: this.listeners.size,
            historySize: this.history.length,
            cacheSize: this.state.ncbiCache?.size || 0,
            mutationsCount: this.state.mutations?.length || 0,
            sequencesCount: this.state.currentFastaSequences?.length || 0
        };
    }
}

// Instance globale
const state = new StateManager();

// Raccourcis pour un accès facile
const getState = (key, defaultValue) => state.get(key, defaultValue);
const setState = (key, value, options) => state.set(key, value, options);
const updateState = (updates, options) => state.update(updates, options);
const subscribeState = (key, callback, options) => state.subscribe(key, callback, options);

// Export pour les modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { StateManager, state, getState, setState, updateState, subscribeState };
}