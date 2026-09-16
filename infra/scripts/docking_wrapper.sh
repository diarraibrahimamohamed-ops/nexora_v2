#!/bin/bash
# Wrapper script pour le docking avec les bonnes permissions
# Ce script s'exécute toujours avec l'utilisateur 

# Variables
SCRIPT_DIR="/tmp"
PYTHON_SCRIPT="$SCRIPT_DIR/docking_from_db_direct.py"
ANALYSIS_ID="$1"
LIGAND_SMILES="$2"

# Vérifier les arguments
if [ -z "$ANALYSIS_ID" ] || [ -z "$LIGAND_SMILES" ]; then
    echo '{"success": false, "error_message": "Arguments manquants: analysis_id et ligand_smiles requis"}'
    exit 1
fi

# Exécuter le script Python avec l'environnement correct
cd "$SCRIPT_DIR"
export DEBUG_PYTHON_PATH=1
export PYTHONPATH="/tmp:/home/empereur/.local/lib/python3.12/site-packages:/usr/local/lib/python3.12/dist-packages:/usr/lib/python3.12/dist-packages"

/usr/bin/python3 "$PYTHON_SCRIPT" "$ANALYSIS_ID" "$LIGAND_SMILES" 2>&1
