#!/bin/bash
# Interim local backup of the weather-arb collector SQLite DB.
#
# Invoked daily by com.nmtrading.weather-arb-backup.plist. Copies the live
# DB to a date-stamped file under the local backup directory. This is an
# INTERIM on-machine backup only; off-machine backup is the separate
# collector-github-backup job, which reads the newest snapshot written here.
#
# Mirrors Tracker_Poly/scripts/launchd/crypto_arb_backup.sh. Uses `VACUUM
# INTO` rather than `sqlite3 .backup`: .backup restarts from page 1 whenever
# another connection commits to the source, which turned the crypto backup
# into a 16h+ job as that DB grew. VACUUM INTO copies from a single WAL read
# snapshot, so concurrent collector commits neither block nor restart it,
# and the output is a defragmented (slightly smaller) copy.
#
# Robustness:
#   - The copy is written to DEST.tmp and renamed onto DEST only after
#     VACUUM INTO succeeds, so a crashed run cannot leave a partial file
#     that looks like a valid snapshot. Any *.tmp present at start is
#     stale (launchd never runs two instances of this label) and removed.
#   - A stale DEST-journal (left by an interrupted run of the old .backup
#     variant) is removed before the rename: SQLite would treat it as a
#     hot journal and roll the fresh snapshot back to empty on first open.
#   - The run aborts up front unless free disk is at least 1.5x the source
#     size, so a backup is never the write that fills the disk under the
#     live collector.
#
# Retention: every snapshot is a full cumulative copy of the append-only
# collector DB, so older snapshots hold no extra data — they only guard
# against corruption propagating into the newest copy. This script had no
# retention until 2026-11-05, by which point the directory held 124
# snapshots (131 GiB) on a full disk. RETAIN=3 keeps three independent
# restore points (~7 GB at current DB size).
set -euo pipefail

SRC="/Users/nicholasmorihisa/trading-data/weather/weather_arb.db"
DEST_DIR="/Users/nicholasmorihisa/trading-data-backup/weather"
STAMP="$(date +%Y-%m-%d)"
DEST="${DEST_DIR}/weather_arb_${STAMP}.db"
TMP="${DEST}.tmp"
RETAIN=3

mkdir -p "${DEST_DIR}"

if [ ! -f "${SRC}" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup ERROR: source DB not found at ${SRC}" >&2
    exit 1
fi

# Stale partials from crashed runs; only one instance runs at a time.
rm -f "${DEST_DIR}"/weather_arb_*.db.tmp "${DEST_DIR}"/weather_arb_*.db.tmp-journal

# Disk guard: need headroom for a full copy plus margin.
src_kb="$(du -k "${SRC}" | cut -f1)"
need_kb=$((src_kb * 3 / 2))
free_kb="$(df -k "${DEST_DIR}" | awk 'NR==2 {print $4}')"
if [ "${free_kb}" -lt "${need_kb}" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup ERROR: ${free_kb}KB free < ${need_kb}KB needed to copy ${SRC}" >&2
    exit 1
fi

start_s="$(date +%s)"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup start: ${SRC} ($(du -h "${SRC}" | cut -f1)) -> ${DEST}"

# Single-pass consistent snapshot (immune to concurrent WAL commits).
sqlite3 "${SRC}" "VACUUM INTO '${TMP}'"

# See header: a stale hot journal under DEST's name would roll the fresh
# snapshot back to empty on first open.
rm -f "${DEST}-journal"
mv -f "${TMP}" "${DEST}"

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup OK: ${SRC} -> ${DEST} ($(du -h "${DEST}" | cut -f1) in $(( $(date +%s) - start_s ))s)"

# Prune all but the RETAIN most-recent backups.
ls -1t "${DEST_DIR}"/weather_arb_*.db 2>/dev/null | tail -n +$((RETAIN + 1)) | while read -r old; do
    rm -f "${old}"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup pruned old snapshot: ${old}"
done
