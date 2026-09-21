#!/usr/bin/env python3
"""Stage 4 supervisor — runs all four long-lived loops as subprocesses.

Spawns and monitors:

1. ``snapshot_runner.py``   — existing 30-min market snapshot (unchanged)
2. ``signal_monitor.py``    — 60 s poll of Euro soccer moneyline books
3. ``paper_trader.py``      — consumes paper_signals → paper_orders + positions
4. ``resolution_watcher.py``— 30-min poll to realize PnL on resolved markets

Each runs in its own subprocess so that a crash in one does not kill
the others. The supervisor polls every 10 s and respawns any child that
has exited.

Why subprocesses (not threads)
------------------------------
- Full state isolation; a memory leak or runaway exception in one loop
  doesn't leak into the others.
- Each child has its own logger namespace so logs are cleanly tagged.
- ``docker logs -f`` shows interleaved output with per-process prefixes
  (the child's own logger setup adds its name to each line).

Heartbeat
---------
The existing ``snapshot_runner.py`` writes ``/app/data/.heartbeat`` on
each successful cycle. The HEALTHCHECK in the Dockerfile reads that
file. Stage 4 does not add a new heartbeat at MVP — a stuck paper
trader is less severe than a stuck snapshot (the backtest data it
needs is already being collected). If all three Stage 4 loops die the
container still reports healthy; this is an accepted MVP trade-off.

Env var pass-through
--------------------
Children inherit the supervisor's environment. Tune individual loops
via the env vars documented in each script's docstring.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s stage4_runner %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("stage4_runner")

REPO_ROOT = Path(__file__).resolve().parent.parent

# (name, argv) — name is used for log prefix and respawn tracking.
CHILDREN: list[tuple[str, list[str]]] = [
    ("snapshot",   [sys.executable, "-u", "scripts/snapshot_runner.py"]),
    ("signal",     [sys.executable, "-u", "scripts/signal_monitor.py"]),
    ("paper",      [sys.executable, "-u", "scripts/paper_trader.py"]),
    ("resolution", [sys.executable, "-u", "scripts/resolution_watcher.py"]),
]

POLL_INTERVAL = 10.0
RESTART_BACKOFF = 30.0  # min seconds between respawns of the same child

procs: dict[str, subprocess.Popen] = {}
last_spawn: dict[str, float] = {}
_shutting_down = False


def spawn(name: str, argv: list[str]) -> None:
    log.info("spawning %s: %s", name, " ".join(argv))
    procs[name] = subprocess.Popen(
        argv,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
    )
    last_spawn[name] = time.monotonic()


def _graceful_shutdown(signum, _frame) -> None:  # noqa: ANN001
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    log.info("signal %d received — terminating children", signum)
    for name, p in procs.items():
        if p.poll() is None:
            log.info("SIGTERM %s (pid=%d)", name, p.pid)
            try:
                p.terminate()
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if all(p.poll() is not None for p in procs.values()):
            break
        time.sleep(0.5)
    for name, p in procs.items():
        if p.poll() is None:
            log.warning("SIGKILL %s (pid=%d) after grace period", name, p.pid)
            try:
                p.kill()
            except ProcessLookupError:
                pass
    sys.exit(0)


def main() -> int:
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    for name, argv in CHILDREN:
        spawn(name, argv)

    log.info("supervisor running — %d child(ren)", len(procs))
    while not _shutting_down:
        time.sleep(POLL_INTERVAL)
        for name, argv in CHILDREN:
            p = procs.get(name)
            if p is None:
                continue
            rc = p.poll()
            if rc is None:
                continue  # still running
            # Child exited; respawn with backoff.
            elapsed = time.monotonic() - last_spawn.get(name, 0)
            if elapsed < RESTART_BACKOFF:
                sleep_more = RESTART_BACKOFF - elapsed
                log.warning(
                    "%s exited (rc=%s), backing off %.0fs before respawn",
                    name, rc, sleep_more,
                )
                time.sleep(sleep_more)
            else:
                log.warning("%s exited (rc=%s), respawning", name, rc)
            spawn(name, argv)

    return 0


if __name__ == "__main__":
    sys.exit(main())
