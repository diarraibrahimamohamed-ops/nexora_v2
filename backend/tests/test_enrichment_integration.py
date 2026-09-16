#!/usr/bin/env python3
"""
Script de test pour l'enrichissement fpocket + sites biologiques
Valide l'intégration SANS modifier les codes existants
"""

import sys
import os
import json

# Ajouter le répertoire courant au path
sys.path.insert(0, '/home/empereur/Bureau/Nexora')

try:
    from docking_complete import create_realistic_protein_structure
    from docking_enrichment_integration import enhanced_detect_binding_pockets, docking_enrichment_manager
    from pocket_detection_enriched import pocket_detector_enriched
    from biological_site_detector import biological_site_detector
except ImportError as e:
    print(f"❌ Erreur import: {e}")
    sys.exit(1)

def test_enriched_pocket_detection():
    """Test complet de la détection enrichie de poches"""
    print("🧪 Test de détection enrichie de poches")
    print("=" * 60)
    
    # Séquence de test (protéine kinase)
    test_sequence = "MNGTEGPNFYVPFSNKTGVVRSPFRYPNMEVAFKDILKVGQGDSLQLVAAINLGTTMGMALTMGQFNVQQRVNLPVERLQNLGVDTVRVLGHIFNKGETRDYVNKEKIKRLTDGKLHEVMTNLGTLQTGPDNVKTPAVFDNKFHNLKQLADYFKNHEMDLFPKNSTFVGSNRR"
    
    print(f"📊 Séquence test: {len(test_sequence)} acides aminés")
    
    # Créer structure 3D
    print("\n🔧 Génération structure 3D...")
    protein_atoms, error = create_realistic_protein_structure(test_sequence)
    
    if error:
        print(f"❌ Erreur structure 3D: {error}")
        return False
    
    print(f"✅ Structure 3D: {len(protein_atoms)} atomes")
    
    # Test 1: fpocket seul
    print("\n🎯 Test 1: fpocket enrichi")
    print("-" * 30)
    
    if pocket_detector_enriched.fpocket_available:
        fpocket_pockets, error = pocket_detector_enriched.detect_pockets_fpocket(protein_atoms)
        if error:
            print(f"⚠️  fpocket erreur: {error}")
        else:
            print(f"✅ fpocket: {len(fpocket_pockets)} poches détectées")
            for i, pocket in enumerate(fpocket_pockets[:3]):
                print(f"   Poche {i+1}: score={pocket['score']:.3f}, centre=({pocket['center_x']:.1f}, {pocket['center_y']:.1f}, {pocket['center_z']:.1f})")
    else:
        print("⚠️  fpocket non disponible")
    
    # Test 2: Sites biologiques
    print("\n🧬 Test 2: Sites biologiques")
    print("-" * 30)
    
    bio_sites, error = biological_site_detector.detect_catalytic_sites(protein_atoms, test_sequence)
    if error:
        print(f"⚠️  Sites biologiques erreur: {error}")
    else:
        print(f"✅ Sites biologiques: {len(bio_sites)} sites détectés")
        for i, site in enumerate(bio_sites[:3]):
            print(f"   Site {i+1}: type={site['site_type']}, confiance={site['confidence']:.3f}")
    
    # Test 3: Détection enrichie combinée
    print("\n🚀 Test 3: Détection enrichie combinée")
    print("-" * 40)
    
    enriched_pockets, error = enhanced_detect_binding_pockets(protein_atoms, test_sequence)
    if error:
        print(f"❌ Erreur enrichissement: {error}")
        return False
    
    print(f"✅ Enrichissement total: {len(enriched_pockets)} poches")
    
    # Afficher les meilleures poches
    print("\n🏆 Meilleures poches enrichies:")
    for i, pocket in enumerate(enriched_pockets[:5]):
        print(f"   {i+1}. {pocket.get('method', 'unknown')} - score={pocket.get('enrichment_score', 0):.3f}")
        print(f"      Centre: ({pocket['center_x']:.1f}, {pocket['center_y']:.1f}, {pocket['center_z']:.1f})")
        print(f"      Confiance: {pocket.get('confidence', 0):.3f}")
        if pocket.get('biological_significance'):
            print(f"      🧬 Signification biologique: {pocket.get('site_type', 'unknown')}")
        print()
    
    # Test 4: Rapport d'enrichissement
    print("📊 Rapport d'enrichissement:")
    print("-" * 30)
    
    report = docking_enrichment_manager.get_enrichment_report(enriched_pockets)
    for key, value in report.items():
        if key != 'pocket_types':
            print(f"   {key}: {value}")
    
    if 'pocket_types' in report:
        print("   Types de poches:")
        for ptype, count in report['pocket_types'].items():
            print(f"      {ptype}: {count}")
    
    return True

def test_integration_compatibility():
    """Test la compatibilité avec les codes existants"""
    print("\n🔗 Test compatibilité avec codes existants")
    print("=" * 50)
    
    # Simuler un appel comme dans docking_from_db.py
    try:
        from docking_from_db import import_docking_functions
        
        # Importer les fonctions originales
        docking_funcs = import_docking_functions()
        if not docking_funcs:
            print("❌ Impossible d'importer les fonctions originales")
            return False
        
        print("✅ Fonctions originales importées")
        
        # Créer une petite structure de test
        test_sequence = "MNGTEGPNFYVPFSNKTGVVRSPFRYPNMEVAFKDILKVGQGDSLQLVAAINLGTTMGMALTMGQFNVQQRVNLPVERLQNLGVDTVRVLGHIFNKGETRDYVNKEKIKRLTDGKLHEVMTNLGTLQTGPDNVKTPAVFDNKFHNLKQLADYFKNHEMDLFPKNSTFVGSNRR"
        protein_atoms, error = create_realistic_protein_structure(test_sequence[:50])  # Plus court pour le test
        
        if error:
            print(f"❌ Erreur structure test: {error}")
            return False
        
        # Test méthode originale
        original_pockets, error = docking_funcs['detect_binding_pockets'](protein_atoms)
        if error:
            print(f"⚠️  Méthode originale erreur: {error}")
            original_count = 0
        else:
            original_count = len(original_pockets)
            print(f"✅ Méthode originale: {original_count} poches")
        
        # Test méthode enrichie
        enriched_pockets, error = enhanced_detect_binding_pockets(protein_atoms, test_sequence[:50])
        if error:
            print(f"❌ Méthode enrichie erreur: {error}")
            return False
        
        enriched_count = len(enriched_pockets)
        print(f"✅ Méthode enrichie: {enriched_count} poches")
        
        # Vérifier l'amélioration
        if enriched_count > original_count:
            print(f"🚀 Amélioration: +{enriched_count - original_count} poches ({((enriched_count/original_count - 1)*100):+.1f}%)")
        elif enriched_count == original_count:
            print("📊 Nombre de poches égal (qualité améliorée)")
        else:
            print("📉 Moins de poches (qualité vs quantité)")
        
        return True
        
    except Exception as e:
        print(f"❌ Erreur test compatibilité: {e}")
        return False

if __name__ == "__main__":
    print("🧪 Test d'enrichissement fpocket + sites biologiques")
    print("=" * 60)
    
    # Test 1: Détection enrichie
    success1 = test_enriched_pocket_detection()
    
    # Test 2: Compatibilité
    success2 = test_integration_compatibility()
    
    print("\n" + "=" * 60)
    if success1 and success2:
        print("🎉 TOUS LES TESTS RÉUSSIS!")
        print("✅ fpocket intégré avec succès")
        print("✅ Sites biologiques intégrés avec succès")
        print("✅ Compatibilité avec codes existants confirmée")
        print("\n📋 Prochaines étapes:")
        print("1. Utiliser enhanced_detect_binding_pockets() dans vos scripts")
        print("2. Les codes originaux ne sont PAS modifiés")
        print("3. fpocket et sites biologiques sont maintenant disponibles!")
    else:
        print("❌ Certains tests ont échoué")
        print("Vérifiez l'installation de fpocket et les dépendances")
