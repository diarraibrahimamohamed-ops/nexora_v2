#!/bin/sh
# Update the NCBI AMRFinderPlus reference database in a persistent storage path.
# Run only in a connected, authorized environment; record the resulting update date.
set -eu

AMRFINDER_BIN="${AMRFINDER_PATH:-/usr/local/bin/amrfinder}"
AMRFINDER_UPDATE_BIN="${AMRFINDER_UPDATE_PATH:-/usr/local/bin/amrfinder_update}"
DB_ROOT="${AMRFINDER_DB_DIR:-/storage/reference/amrfinder}"

if ! command -v "$AMRFINDER_BIN" >/dev/null 2>&1 && [ ! -x "$AMRFINDER_BIN" ]; then
  echo "ERROR: AMRFinderPlus executable not found: $AMRFINDER_BIN" >&2
  exit 2
fi
if ! command -v "$AMRFINDER_UPDATE_BIN" >/dev/null 2>&1 && [ ! -x "$AMRFINDER_UPDATE_BIN" ]; then
  echo "ERROR: amrfinder_update not found: $AMRFINDER_UPDATE_BIN" >&2
  exit 2
fi

mkdir -p "$DB_ROOT"
"$AMRFINDER_UPDATE_BIN" --database "$DB_ROOT"
if [ ! -d "$DB_ROOT/latest" ]; then
  echo "ERROR: expected AMRFinderPlus database at $DB_ROOT/latest after update." >&2
  exit 3
fi
"$AMRFINDER_BIN" --version
printf 'AMRFinderPlus database location: %s\n' "$DB_ROOT/latest"
printf 'Updated at (UTC): %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
