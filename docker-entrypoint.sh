#!/bin/bash
# Polymarket Tracker — docker-compose entrypoint
#
# The compose service overrides CMD with `bash /app/docker-entrypoint.sh`,
# so even though the Dockerfile's default CMD runs stage4_runner directly,
# this script is what actually fires in the production fleet. Keep the
# two in sync.
#
# STAGE4=0 falls back to snapshot-only behavior for the historical
# deployment shape (outer while-loop wrapping a single snapshot call).
# STAGE4=1 (default) runs the full Stage 4 supervisor which spawns
# snapshot_runner + signal_monitor + paper_trader + resolution_watcher.
set -e

if [ "${STAGE4:-1}" = "0" ]; then
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') STAGE4=0 — snapshot-only legacy mode"
    exec python -u scripts/snapshot_runner.py
fi

echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') Stage 4 supervisor starting (snapshot + signal + paper + resolution)"
exec python -u scripts/stage4_runner.py
