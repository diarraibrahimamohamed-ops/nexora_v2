#!/bin/bash
# Script pour corriger les permissions des modules Python pour Apache/XAMPP

echo "=== Correction des permissions pour Apache/XAMPP ==="
echo ""

PYTHON_VERSION=$(python3 --version | grep -oP '\d+\.\d+' | head -1)
SITE_PACKAGES="/home/empereur/.local/lib/python${PYTHON_VERSION}/site-packages"

echo "Version Python: $PYTHON_VERSION"
echo "Dossier site-packages: $SITE_PACKAGES"
echo ""

if [ ! -d "$SITE_PACKAGES" ]; then
    echo "❌ Dossier site-packages non trouvé: $SITE_PACKAGES"
    exit 1
fi

echo "1. Correction des permissions des dossiers..."
find "$SITE_PACKAGES/mysql" -type d -exec chmod o+rx {} \; 2>/dev/null
find "$SITE_PACKAGES" -name "pymysql*" -type d -exec chmod o+rx {} \; 2>/dev/null

echo "2. Correction des permissions des fichiers Python..."
find "$SITE_PACKAGES/mysql" -type f -name "*.py" -exec chmod o+r {} \; 2>/dev/null
find "$SITE_PACKAGES" -name "pymysql*" -type f -name "*.py" -exec chmod o+r {} \; 2>/dev/null

echo "3. Correction des permissions des fichiers binaires (.so)..."
find "$SITE_PACKAGES/mysql" -type f -name "*.so" -exec chmod o+r {} \; 2>/dev/null
find "$SITE_PACKAGES" -name "pymysql*" -type f -name "*.so" -exec chmod o+r {} \; 2>/dev/null

echo ""
echo "✅ Permissions corrigées!"
echo ""
echo "Vérification..."
ls -la "$SITE_PACKAGES/mysql/connector/__init__.py" 2>/dev/null || echo "Fichier non trouvé"
