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
    CRYPTO_ARB_CYCLE_TIMEOUT         hard wall-clock cap for one child cycle
                                     in seconds (default 120). A wedged
                                     network read can never stall the loop:
                                     the child is killed at this cap and the
                                     loop continues to the next cycle.
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
DEFAULT_CYCLE_TIMEOUT = 120    # hard wall-clock cap on a single child cycle


def main() -> int:
    interval = int(os.environ.get("CRYPTO_ARB_INTERVAL", DEFAULT_INTERVAL))
    cycle_timeout = int(
        os.environ.get("CRYPTO_ARB_CYCLE_TIMEOUT", DEFAULT_CYCLE_TIMEOUT))
    db_path = os.environ.get(
        "CRYPTO_ARB_DB",
        "/Users/nicholasmorihisa/trading-data/crypto/crypto_arb.db")
    hb_path = os.environ.get(
        "CRYPTO_ARB_HEARTBEAT",
        "/Users/nicholasmorihisa/trading-data/crypto/.heartbeat_crypto_arb")

    # Absolute path derived from this file's location — robust against the
    # process cwd. The repo root is the parent of scripts/; the child runs
    # with cwd pinned there so its own relative paths resolve too.
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(scripts_dir)
    collect_script = os.path.join(scripts_dir, "crypto_arb_collect.py")

    log.info("crypto_arb_runner starting — interval=%ds cycle_timeout=%ds "
             "db=%s hb=%s", interval, cycle_timeout, db_path, hb_path)

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
                cwd=repo_root,
                timeout=cycle_timeout,
            ).returncode
        except subprocess.TimeoutExpired:
            # The child blew the wall-clock cap (most likely a wedged network
            # read — e.g. a socket killed by a laptop sleep mid-request).
            # subprocess.run has already killed it. Continue to next cycle.
            log.error("cycle %d exceeded %ds wall-clock cap — child killed, "
                      "continuing", cycle, cycle_timeout)
            rc = 1
        except Exception:
            log.exception("cycle %d crashed in the runner", cycle)
            rc = 1

        elapsed = time.time() - t0
        if rc == 0:
            log.info("cycle %d ok in %.1fs", cycle, elapsed)
        else:
            log.warning("cycle %d failed (exit %d) in %.1fs", cycle, rc, elapsed)

        sleep_s = max(interval - elapsed, MIN_SLEEP)
        log.info("sleeping %.1fs until next cycle", sleep_s)
        try:
            time.sleep(sleep_s)
        except Exception:
            log.exception("sleep interrupted; continuing")


if __name__ == "__main__":
    sys.exit(main())
