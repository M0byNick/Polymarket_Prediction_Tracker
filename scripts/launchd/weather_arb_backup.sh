#!/bin/bash
# Interim local backup of the weather-arb collector SQLite DB.
#
# Invoked daily by com.nmtrading.weather-arb-backup.plist. Copies the live
# DB to a date-stamped file under the local backup directory. This is an
# INTERIM on-machine backup only; off-machine backup (e.g. via GitHub) is a
# separate task.
#
# Uses `sqlite3 .backup` rather than a raw `cp` so the snapshot is
# transaction-consistent even while the collector daemon is mid-write
# (the DB runs in WAL mode).
set -euo pipefail

SRC="/Users/nicholasmorihisa/trading-data/weather/weather_arb.db"
DEST_DIR="/Users/nicholasmorihisa/trading-data-backup/weather"
STAMP="$(date +%Y-%m-%d)"
DEST="${DEST_DIR}/weather_arb_${STAMP}.db"

mkdir -p "${DEST_DIR}"

if [ ! -f "${SRC}" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup ERROR: source DB not found at ${SRC}" >&2
    exit 1
fi

# Consistent online backup (safe against concurrent WAL writes).
sqlite3 "${SRC}" ".backup '${DEST}'"

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup OK: ${SRC} -> ${DEST} ($(du -h "${DEST}" | cut -f1))"
