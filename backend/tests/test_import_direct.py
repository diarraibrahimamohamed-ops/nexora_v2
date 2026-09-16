#!/usr/bin/env python3
"""
Test direct d'import MySQL pour diagnostiquer le problème Apache
"""
import sys
import os

# Simuler exactement ce que fait docking_from_db_xampp.py
python_version = f"{sys.version_info.major}.{sys.version_info.minor}"

possible_paths = [
    f'/home/empereur/.local/lib/python{python_version}/site-packages',
    os.path.expanduser(f'~/.local/lib/python{python_version}/site-packages'),
    f'/usr/local/lib/python{python_version}/dist-packages',
    f'/usr/lib/python{python_version}/dist-packages',
]

print("=== DIAGNOSTIC IMPORT MYSQL ===\n")
print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}")
print(f"Utilisateur: {os.getenv('USER', 'unknown')}")
print(f"HOME: {os.path.expanduser('~')}")
print(f"\nChemins à tester:")
for path in possible_paths:
    exists = os.path.exists(path) if path else False
    readable = os.access(path, os.R_OK) if path and exists else False
    print(f"  {path}")
    print(f"    Existe: {exists}, Lisible: {readable}")

print(f"\nsys.path AVANT ajout:")
for p in sys.path[:5]:
    print(f"  - {p}")

# Ajouter les chemins
paths_added = []
for path in possible_paths:
    if path and os.path.exists(path) and path not in sys.path:
        sys.path.insert(0, path)
        paths_added.append(path)

print(f"\nChemins ajoutés: {len(paths_added)}")
for p in paths_added:
    print(f"  ✅ {p}")

print(f"\nsys.path APRÈS ajout (premiers 5):")
for p in sys.path[:5]:
    print(f"  - {p}")

print(f"\n=== TEST IMPORT ===\n")

# Test mysql.connector
try:
    import mysql.connector
    print("✅ mysql.connector importé avec succès")
    print(f"   Emplacement: {mysql.connector.__file__}")
except ImportError as e:
    print(f"❌ Échec import mysql.connector: {e}")
    
    # Test PyMySQL
    try:
        import pymysql
        print("✅ pymysql importé avec succès (fallback)")
        print(f"   Emplacement: {pymysql.__file__}")
    except ImportError as e2:
        print(f"❌ Échec import pymysql: {e2}")
        print(f"\n❌ AUCUN MODULE MYSQL DISPONIBLE")
        print(f"\nVérification fichiers:")
        mysql_path = f'/home/empereur/.local/lib/python{python_version}/site-packages/mysql'
        if os.path.exists(mysql_path):
            print(f"  Dossier mysql existe: {mysql_path}")
            connector_path = os.path.join(mysql_path, 'connector')
            if os.path.exists(connector_path):
                print(f"  Dossier connector existe: {connector_path}")
                init_file = os.path.join(connector_path, '__init__.py')
                if os.path.exists(init_file):
                    print(f"  Fichier __init__.py existe: {init_file}")
                    print(f"  Lisible: {os.access(init_file, os.R_OK)}")
                else:
                    print(f"  ❌ Fichier __init__.py MANQUANT")
            else:
                print(f"  ❌ Dossier connector MANQUANT")
        else:
            print(f"  ❌ Dossier mysql MANQUANT")
