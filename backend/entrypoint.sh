#!/bin/sh
set -e

# Ensure mounted storage directories exist and are writable.
mkdir -p /storage/uploads /storage/results /storage/temp /storage/temp/colabfold

# Best-effort : un fichier root:root 0644 (ex. esmfold_atlas_prediction.pdb)
# bloquait le worker UID 1000. chmod ne doit jamais tuer le process.
chmod 0777 /storage/temp /storage/temp/colabfold 2>/dev/null || true
chmod a+rw /storage/temp/colabfold/* 2>/dev/null || true
chmod 0775 /storage /storage/uploads /storage/results 2>/dev/null || true

# If host UID/GID are provided, make the mounted storage owned by that account.
if [ -n "$HOST_UID" ] && [ -n "$HOST_GID" ]; then
  chown -R "$HOST_UID:$HOST_GID" /storage 2>/dev/null || true
fi

exec "$@"
