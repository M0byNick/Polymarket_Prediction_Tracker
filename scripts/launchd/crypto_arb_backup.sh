#!/bin/bash
# Interim local backup of the crypto-arb collector SQLite DB.
#
# Invoked daily by com.nmtrading.crypto-arb-backup.plist. Copies the live DB
# to a date-stamped file under the local backup directory. This is an
# INTERIM on-machine backup only; off-machine backup (e.g. via GitHub or a
# VPS) is a separate task.
#
# Uses `sqlite3 .backup` rather than a raw `cp` so the snapshot is
# transaction-consistent even while the collector daemon is mid-write (the
# DB runs in WAL mode). A raw cp could capture a torn -wal/-shm pair.
#
# Retention: keeps the 14 most-recent date-stamped backups and prunes older
# ones, so the backup directory does not grow without bound (the crypto DB
# grows faster than the weather DB owing to the tighter 30s cadence).
set -euo pipefail

SRC="/Users/nicholasmorihisa/trading-data/crypto/crypto_arb.db"
DEST_DIR="/Users/nicholasmorihisa/trading-data-backup/crypto"
STAMP="$(date +%Y-%m-%d)"
DEST="${DEST_DIR}/crypto_arb_${STAMP}.db"
RETAIN=14

mkdir -p "${DEST_DIR}"

if [ ! -f "${SRC}" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup ERROR: source DB not found at ${SRC}" >&2
    exit 1
fi

# Consistent online backup (safe against concurrent WAL writes).
sqlite3 "${SRC}" ".backup '${DEST}'"

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup OK: ${SRC} -> ${DEST} ($(du -h "${DEST}" | cut -f1))"

# Prune all but the RETAIN most-recent backups.
ls -1t "${DEST_DIR}"/crypto_arb_*.db 2>/dev/null | tail -n +$((RETAIN + 1)) | while read -r old; do
    rm -f "${old}"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup pruned old snapshot: ${old}"
done
