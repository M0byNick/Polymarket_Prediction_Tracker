"""Logging setup for long-running production loops.

Usage::

    from src.log import get_logger
    log = get_logger("signal_monitor")
    log.info("market %s crossed %.3f", cid, price)

The helper configures the root logger once per process. Calling
``get_logger`` multiple times is safe — only the first call installs
handlers. Output is unbuffered by default (flush after each record) so
that ``docker logs -f`` shows events in real time without waiting for
buffer drain.

Env vars
--------
LOG_LEVEL   Default "INFO". Set to "DEBUG" for verbose output.
LOG_FORMAT  Default "%(asctime)s %(levelname)s %(name)s %(message)s".
"""
from __future__ import annotations

import logging
import os
import sys

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_CONFIGURED = False


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = os.environ.get("LOG_FORMAT", _DEFAULT_FORMAT)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    # Reset handlers so repeated imports in test runners don't double-log.
    root.handlers = [handler]
    root.setLevel(level)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured for unbuffered stdout.

    The first call in the process installs the root handler. Subsequent
    calls are cheap lookups.
    """
    _configure_root()
    return logging.getLogger(name)
