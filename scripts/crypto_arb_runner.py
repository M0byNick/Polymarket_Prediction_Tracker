#!/usr/bin/env python3
"""
Crypto-binary latency arb collector — long-running loop.

Invokes scripts/crypto_arb_collect.py once per CRYPTO_ARB_INTERVAL seconds.
Default interval is 30 seconds: the audit §2.4 specifies tightening to <=30s
cadence before the holdout window opens, and the holdout is imminent (window
2026-06-12 -> 2026-09-03), so the collector runs at 30s from the start.

The interval is measured CYCLE-START to CYCLE-START: if a cycle takes 9s the
loop sleeps 21s, holding a true 30s period. If a cycle ever overruns the
interval the loop sleeps a 5s floor instead of stacking cycles.

Configurable via environment variables:
    CRYPTO_ARB_INTERVAL              seconds between cycle starts (default 30)
    CRYPTO_ARB_DB                    SQLite path (default
                                     /Users/nicholasmorihisa/trading-data/crypto/crypto_arb.db)
    CRYPTO_ARB_HEARTBEAT             heartbeat file path
    CRYPTO_ARB_CAPTURE_HORIZON_MIN   dense-snapshot only markets resolving
                                     within this many minutes (default 90;
                                     a collector-efficiency bound, read by
                                     the child collect script — see
                                     polymarket.DEFAULT_CAPTURE_HORIZON_S)

The child collect process inherits this environment, so the runner does not
need to forward these explicitly.

Each child cycle touches the heartbeat on success. Recommended foreground
startup for a quick local sanity check:

    caffeinate -i python3 -u scripts/crypto_arb_runner.py \
        2>&1 | tee -a /Users/nicholasmorihisa/trading-data/crypto/logs/crypto_arb.log

In production this runner is supervised by the launchd daemon
com.nmtrading.crypto-arb-collector (KeepAlive=true), so `caffeinate` and the
tee are unnecessary there.

OBSERVE-ONLY: the loop only schedules data-collection cycles. No trading.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("crypto_arb_runner")

DEFAULT_INTERVAL = 30          # seconds (pre-reg audit §2.4: <=30s cadence)
MIN_SLEEP = 5                  # floor sleep if a cycle overruns the interval


def main() -> int:
    interval = int(os.environ.get("CRYPTO_ARB_INTERVAL", DEFAULT_INTERVAL))
    db_path = os.environ.get(
        "CRYPTO_ARB_DB",
        "/Users/nicholasmorihisa/trading-data/crypto/crypto_arb.db")
    hb_path = os.environ.get(
        "CRYPTO_ARB_HEARTBEAT",
        "/Users/nicholasmorihisa/trading-data/crypto/.heartbeat_crypto_arb")

    collect_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "crypto_arb_collect.py")

    log.info("crypto_arb_runner starting — interval=%ds db=%s hb=%s",
             interval, db_path, hb_path)

    cycle = 0
    while True:
        cycle += 1
        t0 = time.time()
        log.info("=== cycle %d starting ===", cycle)
        try:
            rc = subprocess.run(
                [sys.executable, "-u", collect_script,
                 "--db", db_path, "--heartbeat", hb_path],
                check=False,
            ).returncode
        except Exception as ex:
            log.error("cycle %d crashed: %s", cycle, ex)
            rc = 1

        elapsed = time.time() - t0
        if rc == 0:
            log.info("cycle %d ok in %.1fs", cycle, elapsed)
        else:
            log.warning("cycle %d failed (exit %d) in %.1fs", cycle, rc, elapsed)

        sleep_s = max(interval - elapsed, MIN_SLEEP)
        log.info("sleeping %.1fs until next cycle", sleep_s)
        time.sleep(sleep_s)


if __name__ == "__main__":
    sys.exit(main())
