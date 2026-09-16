function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('fr-FR', {
day: '2-digit',
month: '2-digit',
year: '2-digit',
hour: '2-digit',
minute: '2-digit'
    });
}

// ====================================================
// FONCTIONS EXISTANTES (maintenues pour compatibilité)
// ====================================================

// Toutes les fonctions existantes du code original sont maintenues...
// (initializeMatrixBackground, DNA/Protein visualization, charts, etc.)
 // Matrix background
function initializeMatrixBackground() {
    const matrixBg = document.getElementById('matrixBg');
    const chars = 'ATCG0123456789';
    
    for (let i = 0; i < 100; i++) {
        const char = document.createElement('div');
        char.textContent = chars[Math.floor(Math.random() * chars.length)];
        char.style.position = 'absolute';
        char.style.left = Math.random() * 100 + '%';
        char.style.top = Math.random() * 100 + '%';
        char.style.color = '#00ffff';
        char.style.fontSize = Math.random() * 20 + 10 + 'px';
        char.style.opacity = Math.random() * 0.5;
        char.style.animation = `float ${Math.random() * 10 + 5}s ease-in-out infinite`;
        matrixBg.appendChild(char);
    }
}

// Variables de contrôles 3D
let dnaControls = null;
let proteinControls = null;

// DNA 3D Visualization
function initializeDNAVisualization() {
    const container = document.getElementById('dnaVisualization');
    if (!container) return;
    
    // Nettoyer d'anciens enfants si réinitialisation
    container.innerHTML = '';
    
    dnaScene = new THREE.Scene();
    const width = Math.max(container.clientWidth, 800);
    const height = Math.max(container.clientHeight, 400);
    dnaCamera = new THREE.PerspectiveCamera(75, width / height, 0.1, 1000);
    dnaRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    dnaRenderer.setSize(width, height);
    dnaRenderer.setClearColor(0x000000, 0);
    container.appendChild(dnaRenderer.domElement);

    const ambientLight = new THREE.AmbientLight(0x404040, 0.6);
    dnaScene.add(ambientLight);
    const directionalLight = new THREE.DirectionalLight(0xffffff, 1);
    directionalLight.position.set(5, 5, 5);
    dnaScene.add(directionalLight);

    if (typeof THREE.OrbitControls !== 'undefined') {
        dnaControls = new THREE.OrbitControls(dnaCamera, dnaRenderer.domElement);
        dnaControls.enableDamping = true;
        dnaControls.dampingFactor = 0.05;
    }

    createDNAModel();
    animateDNA();
}

function resizeDNAVisualization() {
    const container = document.getElementById('dnaVisualization');
    if (!container || !dnaRenderer || !dnaCamera) return;
    const width = container.clientWidth || 800;
    const height = container.clientHeight || 400;
    if (width > 0 && height > 0) {
        dnaCamera.aspect = width / height;
        dnaCamera.updateProjectionMatrix();
        dnaRenderer.setSize(width, height);
    }
}

function onAnalysisTabShow() {
    setTimeout(() => {
        resizeDNAVisualization();
        createDNAModel();
        if (typeof updateComplexityChart === 'function') {
            updateComplexityChart();
        }
    }, 50);
}

function createDNAModel() {
    if (!dnaScene) return;
    
    // Clear existing DNA model
    if (dnaModel) {
        dnaScene.remove(dnaModel);
    }
    
    dnaModel = new THREE.Group();
    
    // Create DNA double helix
    const helixHeight = 8;
    const helixRadius = 2;
    const segments = 60;
    
    for (let i = 0; i < segments; i++) {
        const angle = (i / segments) * Math.PI * 6;
        const y = (i / segments) * helixHeight - helixHeight / 2;
        
        // First strand
        const sphere1 = new THREE.Mesh(
            new THREE.SphereGeometry(0.15, 8, 8),
            new THREE.MeshPhongMaterial({ color: 0x00ffff })
        );
        sphere1.position.set(
            Math.cos(angle) * helixRadius,
            y,
            Math.sin(angle) * helixRadius
        );
        dnaModel.add(sphere1);
        
        // Second strand
        const sphere2 = new THREE.Mesh(
            new THREE.SphereGeometry(0.15, 8, 8),
            new THREE.MeshPhongMaterial({ color: 0xff00ff })
        );
        sphere2.position.set(
            Math.cos(angle + Math.PI) * helixRadius,
            y,
            Math.sin(angle + Math.PI) * helixRadius
        );
        dnaModel.add(sphere2);
        
        // Base pairs
        if (i % 3 === 0) {
            const bond = new THREE.Mesh(
                new THREE.CylinderGeometry(0.05, 0.05, helixRadius * 2, 8),
                new THREE.MeshPhongMaterial({ color: 0xffff00 })
            );
            bond.position.set(0, y, 0);
            bond.rotation.z = Math.PI / 2;
            bond.rotation.y = angle;
            dnaModel.add(bond);
        }
    }
    
    dnaScene.add(dnaModel);
    if (dnaCamera) {
        dnaCamera.position.set(0, 0, 10);
    }
}

function animateDNA() {
    if (!dnaRenderer || !dnaScene || !dnaCamera) return;
    requestAnimationFrame(animateDNA);
    
    if (dnaModel) {
        dnaModel.rotation.y += 0.01;
        dnaModel.rotation.x += 0.005;
    }

    if (dnaControls) {
        dnaControls.update();
    }
    
    dnaRenderer.render(dnaScene, dnaCamera);
}

// Protein 3D Visualization
function initializeProteinVisualization() {
    const container = document.getElementById('proteinVisualization');
    if (!container) return;
    
    container.innerHTML = '';
    
    proteinScene = new THREE.Scene();
    const width = Math.max(container.clientWidth, 800);
    const height = Math.max(container.clientHeight, 400);
    proteinCamera = new THREE.PerspectiveCamera(75, width / height, 0.1, 1000);
    proteinRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    proteinRenderer.setSize(width, height);
    proteinRenderer.setClearColor(0x000000, 0);
    container.appendChild(proteinRenderer.domElement);

    const ambientLight = new THREE.AmbientLight(0x404040, 0.6);
    proteinScene.add(ambientLight);
    const directionalLight = new THREE.DirectionalLight(0xffffff, 1);
    directionalLight.position.set(5, 5, 5);
    proteinScene.add(directionalLight);

    if (typeof THREE.OrbitControls !== 'undefined') {
        proteinControls = new THREE.OrbitControls(proteinCamera, proteinRenderer.domElement);
        proteinControls.enableDamping = true;
        proteinControls.dampingFactor = 0.05;
    }

    createProteinModel();
    animateProtein();
}

function resizeProteinVisualization() {
    const container = document.getElementById('proteinVisualization');
    if (!container || !proteinRenderer || !proteinCamera) return;
    const width = container.clientWidth || 800;
    const height = container.clientHeight || 400;
    if (width > 0 && height > 0) {
        proteinCamera.aspect = width / height;
        proteinCamera.updateProjectionMatrix();
        proteinRenderer.setSize(width, height);
    }
}

function onProteinTabShow() {
    setTimeout(() => {
        resizeProteinVisualization();
        createProteinModel();
    }, 50);
}

function createProteinModel() {
    if (!proteinScene) return;
    
    if (proteinModel) {
        proteinScene.remove(proteinModel);
        proteinModel = null;
    }
    
    // Ne pas afficher de modèle si aucune protéine n'a été traduite par l'analyse
    if (!currentProtein || currentProtein.length === 0) {
        // Afficher un message placeholder dans le conteneur
        const container = document.getElementById('proteinVisualization');
        if (container) {
            // Créer ou mettre à jour le message overlay
            let overlay = container.querySelector('.protein-placeholder');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.className = 'protein-placeholder';
                overlay.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;display:flex;align-items:center;justify-content:center;z-index:10;pointer-events:none;';
                overlay.innerHTML = '<div style="text-align:center;color:#00ffff;opacity:0.6;"><i class="fas fa-dna" style="font-size:3rem;margin-bottom:1rem;display:block;"></i><p>Lancez une analyse pour visualiser la structure protéique</p></div>';
                container.style.position = 'relative';
                container.appendChild(overlay);
            }
        }
        return;
    }
    
    // Supprimer le placeholder si présent
    const container = document.getElementById('proteinVisualization');
    if (container) {
        const overlay = container.querySelector('.protein-placeholder');
        if (overlay) overlay.remove();
    }
    
    proteinModel = new THREE.Group();
    
    const aminoAcids = currentProtein.split('');
    const radius = 3;
    
    aminoAcids.forEach((aa, index) => {
        if (aa === '*') return; // rencontre d'un codon stop
        
        const props = aminoAcidProperties[aa] || { name: 'Unknown', mass: 100, type: 'hydrophobic' };
        
        const angle = (index / aminoAcids.length) * Math.PI * 2;
        const y = Math.sin(index * 0.3) * 2;
        
        let color;
        switch (props.type) {
            case 'hydrophobic': color = 0xff6b6b; break;
            case 'hydrophilic': color = 0x4ecdc4; break;
            case 'polar': color = 0x45b7d1; break;
            case 'charged': color = 0xf9ca24; break;
            default: color = 0x00ffff;
        }
        
        const sphere = new THREE.Mesh(
            new THREE.SphereGeometry(0.25, 12, 12),
            new THREE.MeshPhongMaterial({ color: color })
        );
        
        sphere.position.set(
            Math.cos(angle) * radius,
            y,
            Math.sin(angle) * radius
        );
        
        proteinModel.add(sphere);
        
        // Add connections
        if (index < aminoAcids.length - 1) {
            const nextAngle = ((index + 1) / aminoAcids.length) * Math.PI * 2;
            const nextY = Math.sin((index + 1) * 0.3) * 2;
            
            const connection = new THREE.Mesh(
                new THREE.CylinderGeometry(0.04, 0.04, 0.5, 8),
                new THREE.MeshPhongMaterial({ color: 0x888888 })
            );
            
            connection.position.set(
                (Math.cos(angle) * radius + Math.cos(nextAngle) * radius) / 2,
                (y + nextY) / 2,
                (Math.sin(angle) * radius + Math.sin(nextAngle) * radius) / 2
            );
            
            proteinModel.add(connection);
        }
    });
    
    proteinScene.add(proteinModel);
    if (proteinCamera) {
        proteinCamera.position.set(0, 0, 10);
    }
}

function animateProtein() {
    if (!proteinRenderer || !proteinScene || !proteinCamera) return;
    if (!window.__nexoraProteinAnimationStarted) {
        window.__nexoraProteinAnimationStarted = true;
    }
    requestAnimationFrame(animateProtein);
    
    if (proteinModel) {
        proteinModel.rotation.y += 0.008;
        proteinModel.rotation.x += 0.003;
    }

    if (proteinControls) {
        proteinControls.update();
    }
    
    proteinRenderer.render(proteinScene, proteinCamera);
}

// Redimensionnement réactif lors des réajustements de fenêtre
window.addEventListener('resize', function() {
    if (document.getElementById('analysis')?.classList.contains('active')) {
        resizeDNAVisualization();
    }
    if (document.getElementById('protein3d')?.classList.contains('active')) {
        resizeProteinVisualization();
    }
});

// Exports globaux
window.resizeDNAVisualization = resizeDNAVisualization;
window.resizeProteinVisualization = resizeProteinVisualization;
window.onAnalysisTabShow = onAnalysisTabShow;
window.onProteinTabShow = onProteinTabShow;
window.createDNAModel = createDNAModel;
window.createProteinModel = createProteinModel;
window.initializeDNAVisualization = initializeDNAVisualization;
window.initializeProteinVisualization = initializeProteinVisualization;

// Charts initialization
function initializeCharts() {
    initializeNucleotideChart();
    initializeRNAChart();
    initializeResistanceChart();
    initializeAminoAcidChart();
}

function initializeNucleotideChart() {
    const ctx = document.getElementById('nucleotideChart');
    if (!ctx) 
        return;
    
    nucleotideChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Adénine (A)', 'Thymine (T)', 'Guanine (G)', 'Cytosine (C)'],
            datasets: [{
                label: 'Fréquence',
                data: [0, 0, 0, 0],
                backgroundColor: [
                    'rgba(255, 107, 107, 0.8)',
                    'rgba(78, 205, 196, 0.8)',
                    'rgba(255, 230, 109, 0.8)',
                    'rgba(149, 225, 211, 0.8)'
                ],
                borderColor: [
                    'rgba(255, 107, 107, 1)',
                    'rgba(78, 205, 196, 1)',
                    'rgba(255, 230, 109, 1)',
                    'rgba(149, 225, 211, 1)'
                ],
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#00ffff' }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { color: '#00ffff' },
                    grid: { color: 'rgba(0, 255, 255, 0.1)' }
                },
                x: {
                    ticks: { color: '#00ffff' },
                    grid: { color: 'rgba(0, 255, 255, 0.1)' }
                }
            }
        }
    });
}

function initializeRNAChart() {
    const ctx = document.getElementById('rnaChart');
    if (!ctx) return;
    
    rnaChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Adénine (A)', 'Uracile (U)', 'Guanine (G)', 'Cytosine (C)'],
            datasets: [{
                data: [0, 0, 0, 0],
                backgroundColor: [
                    'rgba(255, 107, 107, 0.8)',
                    'rgba(255, 140, 66, 0.8)',
                    'rgba(255, 230, 109, 0.8)',
                    'rgba(149, 225, 211, 0.8)'
                ],
                borderColor: [
                    'rgba(255, 107, 107, 1)',
                    'rgba(255, 140, 66, 1)',
                    'rgba(255, 230, 109, 1)',
                    'rgba(149, 225, 211, 1)'
                ],
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#00ffff' }
                }
            }
        }
    });
}

function initializeResistanceChart() {
    const ctx = document.getElementById('resistanceProfileChart');
    if (!ctx) return;
    
    resistanceChart = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: ['Pénicilline', 'Tétracycline', 'Chloramphénicol', 'Streptomycine', 'Rifampicine', 'Vancomycine'],
            datasets: [{
                label: 'Résistance (%)',
                data: [0, 0, 0, 0, 0, 0],
                backgroundColor: 'rgba(255, 0, 0, 0.2)',
                borderColor: 'rgba(255, 0, 0, 1)',
                borderWidth: 2,
                pointBackgroundColor: 'rgba(255, 0, 0, 1)',
                pointBorderColor: '#fff',
                pointHoverBackgroundColor: '#fff',
                pointHoverBorderColor: 'rgba(255, 0, 0, 1)'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#00ffff' }
                }
            },
            scales: {
                r: {
                    beginAtZero: true,
                    max: 100,
                    ticks: { color: '#00ffff' },
                    grid: { color: 'rgba(0, 255, 255, 0.1)' },
                    pointLabels: { color: '#00ffff' }
                }
            }
        }
    });
}

function initializeAminoAcidChart() {
    const ctx = document.getElementById('aminoAcidChart');
    if (!ctx) return;
    
    aminoAcidChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: [],
            datasets: [{
                label: 'Fréquence',
                data: [],
                backgroundColor: 'rgba(0, 255, 255, 0.8)',
                borderColor: 'rgba(0, 255, 255, 1)',
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#00ffff' }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { color: '#00ffff' },
                    grid: { color: 'rgba(0, 255, 255, 0.1)' }
                },
                x: {
                    ticks: { color: '#00ffff' },
                    grid: { color: 'rgba(0, 255, 255, 0.1)' }
                }
            }
        }
    });
    
}

 

// Ajout des fonctions manquantes pour la compatibilité complète
