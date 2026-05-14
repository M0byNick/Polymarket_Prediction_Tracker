#!/usr/bin/env python3
"""
Long-running entry point — invokes weather_arb_collect.py every WEATHER_ARB_INTERVAL
seconds. Mirrors the pattern of scripts/snapshot_runner.py.

Configurable via environment variables:
    WEATHER_ARB_INTERVAL   seconds between cycles (default 1800 = 30 min)
    WEATHER_ARB_DB         database path (default ./data/weather_forecast_arb.db)
    WEATHER_ARB_HEARTBEAT  heartbeat file (default ./data/.heartbeat_weather_arb)

Touches the heartbeat after each successful cycle. Recommended local startup:

    # in repo root
    mkdir -p data
    caffeinate -i python3 -u scripts/weather_arb_runner.py \
        2>&1 | tee -a data/weather_arb.log

The `-u` keeps stdout unbuffered (live progress lines). `caffeinate -i` keeps
the laptop awake while the runner is in the foreground; drop it once you
migrate to the VPS.
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
log = logging.getLogger("weather_arb_runner")

DEFAULT_INTERVAL = 1800  # 30 min, matches snapshot_runner.py


def main() -> int:
    interval = int(os.environ.get("WEATHER_ARB_INTERVAL", DEFAULT_INTERVAL))
    db_path = os.environ.get("WEATHER_ARB_DB", "data/weather_forecast_arb.db")
    hb_path = os.environ.get("WEATHER_ARB_HEARTBEAT", "data/.heartbeat_weather_arb")

    log.info("weather_arb_runner starting — interval=%ds db=%s hb=%s",
             interval, db_path, hb_path)

    cycle = 0
    while True:
        cycle += 1
        t0 = time.time()
        log.info("=== cycle %d starting ===", cycle)
        try:
            rc = subprocess.run(
                [sys.executable, "-u", "scripts/weather_arb_collect.py",
                 "--db", db_path, "--heartbeat", hb_path],
                check=False,
            ).returncode
        except Exception as ex:
            log.error("cycle %d crashed: %s", cycle, ex)
            rc = 1

        elapsed = time.time() - t0
        if rc == 0:
            log.info("cycle %d ok in %.0fs", cycle, elapsed)
        else:
            log.warning("cycle %d failed (exit %d) in %.0fs", cycle, rc, elapsed)

        sleep_s = max(interval - elapsed, 10)
        log.info("sleeping %.0fs until next cycle", sleep_s)
        time.sleep(sleep_s)


if __name__ == "__main__":
    sys.exit(main())
