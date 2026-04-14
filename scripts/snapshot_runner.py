#!/usr/bin/env python3
"""
Docker entry point — runs snapshot_markets.py in a loop.

Configurable via environment variables:
    SNAPSHOT_INTERVAL  seconds between cycles (default 1800 = 30 min)
    SNAPSHOT_HOURS     fast-settling horizon (default 72)
    SNAPSHOT_RATE      API requests/sec (default 10.0)
    SNAPSHOT_MIN_VOL   min 24h volume USD (default 500)
    SNAPSHOT_MIN_LIQ   min liquidity USD (default 500)
    SNAPSHOT_DB        database path (default /app/data/polymarket_snapshots.db)

Touches /app/data/.heartbeat after each successful cycle for Docker
HEALTHCHECK.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("runner")

HEARTBEAT_PATH = Path("/app/data/.heartbeat")
DEFAULT_INTERVAL = 1800  # 30 min


def run_cycle() -> int:
    """Run one snapshot cycle. Returns the subprocess exit code."""
    cmd = [
        sys.executable, "-u", "scripts/snapshot_markets.py",
        "--db", os.environ.get("SNAPSHOT_DB", "/app/data/polymarket_snapshots.db"),
        "--rate", os.environ.get("SNAPSHOT_RATE", "10.0"),
        "--hours", os.environ.get("SNAPSHOT_HOURS", "72"),
        "--min-vol-24h", os.environ.get("SNAPSHOT_MIN_VOL", "500"),
        "--min-liq", os.environ.get("SNAPSHOT_MIN_LIQ", "500"),
    ]
    result = subprocess.run(cmd)
    return result.returncode


def main():
    interval = int(os.environ.get("SNAPSHOT_INTERVAL", DEFAULT_INTERVAL))
    log.info("snapshot_runner starting — interval=%ds", interval)

    cycle = 0
    while True:
        cycle += 1
        log.info("=== cycle %d starting ===", cycle)
        t0 = time.time()

        try:
            rc = run_cycle()
        except Exception as ex:
            log.error("cycle %d crashed: %s", cycle, ex)
            rc = 1

        elapsed = time.time() - t0

        if rc == 0:
            HEARTBEAT_PATH.touch()
            log.info("cycle %d completed ok in %.0fs — heartbeat touched", cycle, elapsed)
        else:
            log.warning("cycle %d failed (exit %d) in %.0fs — heartbeat NOT touched", cycle, rc, elapsed)

        # Sleep until next cycle (subtract elapsed if cycle took less than interval)
        sleep_time = max(interval - elapsed, 10)
        log.info("sleeping %.0fs until next cycle", sleep_time)
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
