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

// DNA 3D Visualization
function initializeDNAVisualization() {
    const container = document.getElementById('dnaVisualization');
    if (!container) return;
    
    dnaScene = new THREE.Scene();
    const width = Math.max(container.clientWidth, 1);
    const height = Math.max(container.clientHeight, 1);
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

    createDNAModel();
    animateDNA();
}

function createDNAModel() {
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
    dnaCamera.position.set(0, 0, 10);
}

function animateDNA() {
    if (!dnaRenderer || !dnaScene || !dnaCamera) return;
    requestAnimationFrame(animateDNA);
    
    if (dnaModel) {
        dnaModel.rotation.y += 0.01;
        dnaModel.rotation.x += 0.005;
    }
    
    dnaRenderer.render(dnaScene, dnaCamera);
}

// Protein 3D Visualization
function initializeProteinVisualization() {
    const container = document.getElementById('proteinVisualization');
    if (!container) return;
    
    proteinScene = new THREE.Scene();
    const width = Math.max(container.clientWidth, 1);
    const height = Math.max(container.clientHeight, 1);
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

    createProteinModel();
    animateProtein();
}

function createProteinModel() {
    if (proteinModel) {
        proteinScene.remove(proteinModel);
    }
    
    proteinModel = new THREE.Group();
    
    // Creation de de la structure proteique en fonction de la séquence proteique
    if (currentProtein) {
        const aminoAcids = currentProtein.split('');
        const radius = 3;
        
        aminoAcids.forEach((aa, index) => {
            if (aa === '*') return; // rencontre d'un codon stop
            
            const props = aminoAcidProperties[aa];
            if (!props) return;
            
            const angle = (index / aminoAcids.length) * Math.PI * 2;
            const y = Math.sin(index * 0.2) * 2;
            
            let color;
            switch (props.type) {
                case 'hydrophobic': color = 0xff6b6b; break;
                case 'hydrophilic': color = 0x4ecdc4; break;
                case 'polar': color = 0x45b7d1; break;
                case 'charged': color = 0xf9ca24; break;
                default: color = 0xffffff;
            }
            
            const sphere = new THREE.Mesh(
                new THREE.SphereGeometry(0.2, 8, 8),
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
                const nextY = Math.sin((index + 1) * 0.2) * 2;
                
                const connection = new THREE.Mesh(
                    new THREE.CylinderGeometry(0.02, 0.02, 0.5, 8),
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
    }
    
    proteinScene.add(proteinModel);
    proteinCamera.position.set(0, 0, 10);
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
    
    proteinRenderer.render(proteinScene, proteinCamera);
}

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
