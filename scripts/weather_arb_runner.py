#!/usr/bin/env python3
"""
Long-running entry point — invokes weather_arb_collect.py every WEATHER_ARB_INTERVAL
seconds. Mirrors the pattern of scripts/snapshot_runner.py.

Configurable via environment variables:
    WEATHER_ARB_INTERVAL        seconds between cycles (default 1800 = 30 min)
    WEATHER_ARB_DB              database path (default ./data/weather_forecast_arb.db)
    WEATHER_ARB_HEARTBEAT       heartbeat file (default ./data/.heartbeat_weather_arb)
    WEATHER_ARB_CYCLE_TIMEOUT   hard wall-clock cap for one child cycle in
                                seconds (default 600 = 10 min)

Touches the heartbeat after each successful cycle. Recommended local startup:

    # in repo root
    mkdir -p data
    caffeinate -i python3 -u scripts/weather_arb_runner.py \
        2>&1 | tee -a data/weather_arb.log

The `-u` keeps stdout unbuffered (live progress lines). `caffeinate -i` keeps
the laptop awake while the runner is in the foreground; drop it once you
migrate to the VPS.

RESILIENCE INVARIANT (added 2026-05-19 after a ~20h silent stall):
  A bad cycle must cost exactly one cycle, never the collector. To guarantee
  that, this loop:
    * resolves the child collect script by an ABSOLUTE path derived from
      __file__, so the loop cannot silently no-op every cycle if the process
      cwd is not the repo root (the original relative "scripts/..." path did
      exactly that for ~20h after the working copy was checked out onto a
      different branch);
    * runs the child with `cwd` pinned to the repo root, so the child's own
      relative paths resolve regardless of how the runner was launched;
    * imposes a hard wall-clock timeout on the child via subprocess.run —
      if a network read wedges (e.g. a socket killed by a laptop sleep
      mid-request), the child is killed and the loop moves on;
    * catches EVERY exception per cycle, logs it, and continues to the next
      cycle after the normal sleep.
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

DEFAULT_INTERVAL = 1800       # 30 min, matches snapshot_runner.py
DEFAULT_CYCLE_TIMEOUT = 600   # 10 min hard cap on a single child cycle
MIN_SLEEP = 10                # floor sleep if a cycle overruns the interval


def main() -> int:
    interval = int(os.environ.get("WEATHER_ARB_INTERVAL", DEFAULT_INTERVAL))
    cycle_timeout = int(
        os.environ.get("WEATHER_ARB_CYCLE_TIMEOUT", DEFAULT_CYCLE_TIMEOUT))
    db_path = os.environ.get("WEATHER_ARB_DB", "data/weather_forecast_arb.db")
    hb_path = os.environ.get("WEATHER_ARB_HEARTBEAT", "data/.heartbeat_weather_arb")

    # Resolve the child script + repo root absolutely from this file's
    # location. This is the crypto_arb_runner.py pattern and is the single
    # most important resilience fix: a relative "scripts/weather_arb_collect.py"
    # is resolved against the process cwd, so it breaks the instant the cwd
    # is not the repo root (or, as happened here, the file is swapped out by
    # a branch checkout) — and it breaks SILENTLY, one no-op cycle at a time.
    scripts_dir = Path(__file__).resolve().parent
    repo_root = scripts_dir.parent
    collect_script = scripts_dir / "weather_arb_collect.py"

    log.info("weather_arb_runner starting — interval=%ds cycle_timeout=%ds "
             "db=%s hb=%s collect=%s",
             interval, cycle_timeout, db_path, hb_path, collect_script)

    if not collect_script.is_file():
        # Fail loudly and let launchd KeepAlive restart us, rather than
        # looping forever doing nothing. A missing collect script is an
        # install error, not a transient cycle failure.
        log.error("collect script not found at %s — aborting so the "
                  "supervisor can surface the install error", collect_script)
        return 1

    cycle = 0
    while True:
        cycle += 1
        t0 = time.time()
        log.info("=== cycle %d starting ===", cycle)
        rc: int
        try:
            rc = subprocess.run(
                [sys.executable, "-u", str(collect_script),
                 "--db", db_path, "--heartbeat", hb_path],
                check=False,
                cwd=str(repo_root),
                timeout=cycle_timeout,
            ).returncode
        except subprocess.TimeoutExpired:
            # The child blew the wall-clock cap (most likely a wedged network
            # read). subprocess.run has already killed it. Treat as a failed
            # cycle and continue — the bad cycle cost one cycle, not the loop.
            log.error("cycle %d exceeded %ds wall-clock cap — child killed, "
                      "continuing to next cycle", cycle, cycle_timeout)
            rc = 1
        except Exception:
            # Any other failure to even launch/track the child. Never let
            # this escape the loop.
            log.exception("cycle %d crashed in the runner", cycle)
            rc = 1

        elapsed = time.time() - t0
        if rc == 0:
            log.info("cycle %d ok in %.0fs", cycle, elapsed)
        else:
            log.warning("cycle %d failed (exit %d) in %.0fs", cycle, rc, elapsed)

        sleep_s = max(interval - elapsed, MIN_SLEEP)
        log.info("sleeping %.0fs until next cycle", sleep_s)
        try:
            time.sleep(sleep_s)
        except Exception:
            # Defensive: never let an interrupted sleep kill the loop.
            log.exception("sleep interrupted; continuing")


if __name__ == "__main__":
    sys.exit(main())
