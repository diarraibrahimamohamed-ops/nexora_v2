function showTab(tabName) {
    const targetContent = document.getElementById(tabName);
    if (!targetContent) {
        console.warn(`[showTab] Onglet introuvable: ${tabName}`);
        return;
    }

    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.toggle('active', tab === targetContent);
    });

    document.querySelectorAll('.tab').forEach(tab => {
        const onclick = tab.getAttribute('onclick') || '';
        tab.classList.toggle('active', onclick.includes(`showTab('${tabName}')`));
    });

    if (tabName === 'analysis' && typeof window.onAnalysisTabShow === 'function') {
        setTimeout(() => window.onAnalysisTabShow(), 50);
    } else if (tabName === 'protein3d' && typeof window.onProteinTabShow === 'function') {
        setTimeout(() => window.onProteinTabShow(), 50);
    } else if (tabName === 'docking' && typeof window.onDockingTabShow === 'function') {
        setTimeout(() => window.onDockingTabShow(), 50);
    }
}
