#!/usr/bin/env python3
"""Stage 4 signal monitor — fade-favorite Euro soccer moneyline NO.

Polls open Polymarket markets on a short cadence. For each market that
passes the primary-model gate (Euro top-flight "Will [team] win on
<date>?" moneyline, resolving within 72h, not already in an open paper
position), fetches the NO token's order book and emits a
``paper_signals`` row when the best ask has crossed ``NO_THRESHOLD``.

The loop is deliberately conservative:

- **One signal per condition_id per resolution cycle.** We check for an
  existing open position OR pending new signal before emitting.
- **Ask-size gate.** The ask must offer at least ``MIN_ASK_SIZE_USD``
  notional. Without this filter we'd fire on 1-share dust quotes that
  don't reflect real liquidity.
- **60 s default cadence.** Entry prices for Euro soccer moneylines
  move minute-to-minute in the final hour, so coarser polling would
  miss signals. Shorter is fine but Gamma pagination takes ~6-10 s on
  cold cache so sub-30 s is pointless.

Env vars
--------
NO_THRESHOLD           Default 0.35. Buy NO when best_ask <= this.
MIN_ASK_SIZE_USD       Default 100. Required notional at best ask.
FAST_SETTLING_HOURS    Default 72. Only monitor markets resolving within this.
SIGNAL_POLL_SECONDS    Default 60. Loop cadence.
SIGNAL_DB_PATH         Default data/polymarket_snapshots.db.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make 'src' importable when run as a top-level script.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.classifier import is_primary_model_target  # noqa: E402
from src.db import connect  # noqa: E402
from src.log import get_logger  # noqa: E402
from src import paper_schema  # noqa: E402,F401  (registers DDL)
from src.polymarket_client import PolymarketClient, top_of_book  # noqa: E402

log = get_logger("signal_monitor")

DEFAULT_DB_PATH = "data/polymarket_snapshots.db"
DEFAULT_THRESHOLD = 0.35
DEFAULT_MIN_ASK_SIZE_USD = 100.0
DEFAULT_FAST_SETTLING_HOURS = 72
DEFAULT_POLL_SECONDS = 60


def _now_unix() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _parse_resolve_ts(market: dict) -> int | None:
    end = market.get("endDate") or market.get("endDateIso")
    if not end:
        return None
    try:
        return int(
            datetime.fromisoformat(str(end).replace("Z", "+00:00")).timestamp()
        )
    except ValueError:
        return None


def _extract_no_token(market: dict) -> str | None:
    """Return the CLOB token ID for the NO side.

    Polymarket's convention: ``clobTokenIds[0]`` is YES, ``[1]`` is NO.
    Sometimes the field is a JSON-encoded string; normalize both shapes.
    """
    tokens = market.get("clobTokenIds")
    if isinstance(tokens, str):
        import json

        try:
            tokens = json.loads(tokens)
        except ValueError:
            return None
    if not isinstance(tokens, list) or len(tokens) < 2:
        return None
    return tokens[1] or None


def scan_once(
    client: PolymarketClient,
    db_path: str,
    threshold: float,
    min_ask_size_usd: float,
    fast_settling_hours: int,
) -> dict:
    """One pass of the monitor. Returns counters for logging."""
    now = _now_unix()
    cutoff = now + fast_settling_hours * 3600

    stats = {
        "scanned": 0,
        "target_markets": 0,
        "skipped_dup": 0,
        "skipped_resolve": 0,
        "book_fetches": 0,
        "threshold_hits": 0,
        "size_rejects": 0,
        "signals_written": 0,
    }

    # Two-pass so we only open the DB once per scan (avoids WAL
    # contention with the resolution watcher).
    candidates: list[tuple[dict, int, str]] = []
    for m in client.iter_open_markets():
        stats["scanned"] += 1
        slug = m.get("slug") or ""
        title = m.get("question") or ""
        if not is_primary_model_target(slug, title):
            continue
        resolve_ts = _parse_resolve_ts(m)
        if resolve_ts is None or resolve_ts <= now or resolve_ts > cutoff:
            stats["skipped_resolve"] += 1
            continue
        no_token = _extract_no_token(m)
        if not no_token:
            continue
        candidates.append((m, resolve_ts, no_token))
        stats["target_markets"] += 1

    if not candidates:
        return stats

    # Filter out markets we've already signaled or hold a position on.
    with connect(db_path) as conn:
        kept: list[tuple[dict, int, str]] = []
        for m, resolve_ts, no_token in candidates:
            cid = m.get("conditionId")
            if not cid:
                continue
            if paper_schema.has_open_or_recent_position(conn, cid):
                stats["skipped_dup"] += 1
                continue
            kept.append((m, resolve_ts, no_token))

    # Book fetches happen outside the DB context to avoid holding a
    # connection open during network I/O.
    signals_to_write: list[tuple] = []
    for m, resolve_ts, no_token in kept:
        book = client.get_book(no_token)
        stats["book_fetches"] += 1
        if book is None:
            continue
        bid_p, bid_s, ask_p, ask_s = top_of_book(book)
        if ask_p is None:
            continue
        if ask_p > threshold:
            continue
        stats["threshold_hits"] += 1
        # Size gate: ask_size is in contracts and each contract pays $1
        # at resolution. Required USD at the ask = ask_size * ask_price.
        if (ask_s or 0) * ask_p < min_ask_size_usd:
            stats["size_rejects"] += 1
            continue
        mid = (
            (bid_p + ask_p) / 2.0
            if bid_p is not None and ask_p is not None
            else (bid_p if bid_p is not None else ask_p)
        )
        spread = (ask_p - bid_p) if (bid_p is not None and ask_p is not None) else None
        from src.classifier import classify_slug

        signals_to_write.append(
            (
                m["conditionId"],
                no_token,
                "NO",
                ask_p,              # trigger_price
                bid_p,
                ask_p,
                bid_s,
                ask_s,
                mid,
                spread,
                m.get("slug") or "",
                m.get("question") or "",
                classify_slug(m.get("slug") or ""),
                resolve_ts,
                "new",
                None,
                _now_unix(),
                None,
            )
        )

    if signals_to_write:
        with connect(db_path) as conn:
            conn.executemany(
                """
                INSERT INTO paper_signals (
                    condition_id, token_id, side, trigger_price,
                    best_bid, best_ask, bid_size, ask_size,
                    book_mid, book_spread, slug, title, category_v2,
                    resolve_ts, status, reject_reason, created_ts, consumed_ts
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                signals_to_write,
            )
            conn.commit()
        stats["signals_written"] = len(signals_to_write)

    return stats


def loop(
    db_path: str = DEFAULT_DB_PATH,
    threshold: float = DEFAULT_THRESHOLD,
    min_ask_size_usd: float = DEFAULT_MIN_ASK_SIZE_USD,
    fast_settling_hours: int = DEFAULT_FAST_SETTLING_HOURS,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
) -> None:
    """Run forever. Each iteration does one :func:`scan_once`."""
    client = PolymarketClient()
    log.info(
        "signal_monitor starting: db=%s threshold=%.2f min_size=$%d horizon=%dh poll=%ds",
        db_path, threshold, int(min_ask_size_usd), fast_settling_hours, poll_seconds,
    )
    while True:
        t0 = time.monotonic()
        try:
            stats = scan_once(
                client, db_path, threshold, min_ask_size_usd, fast_settling_hours
            )
            dt = time.monotonic() - t0
            log.info(
                "scan done in %.1fs: scanned=%d targets=%d dup=%d "
                "books=%d hits=%d size_rej=%d signals=%d",
                dt, stats["scanned"], stats["target_markets"],
                stats["skipped_dup"], stats["book_fetches"],
                stats["threshold_hits"], stats["size_rejects"],
                stats["signals_written"],
            )
        except Exception as ex:  # noqa: BLE001  — long-running loop: don't crash
            log.exception("scan failed: %s", ex)

        elapsed = time.monotonic() - t0
        sleep_for = max(1.0, poll_seconds - elapsed)
        time.sleep(sleep_for)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("SIGNAL_DB_PATH", DEFAULT_DB_PATH))
    parser.add_argument(
        "--threshold",
        type=float,
        default=float(os.environ.get("NO_THRESHOLD", DEFAULT_THRESHOLD)),
    )
    parser.add_argument(
        "--min-ask-size-usd",
        type=float,
        default=float(os.environ.get("MIN_ASK_SIZE_USD", DEFAULT_MIN_ASK_SIZE_USD)),
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=int(os.environ.get("FAST_SETTLING_HOURS", DEFAULT_FAST_SETTLING_HOURS)),
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=int(os.environ.get("SIGNAL_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan and exit (for smoke testing).",
    )
    args = parser.parse_args()

    if args.once:
        client = PolymarketClient()
        stats = scan_once(
            client, args.db, args.threshold, args.min_ask_size_usd, args.hours
        )
        log.info("one-shot stats: %s", stats)
        return 0

    loop(
        db_path=args.db,
        threshold=args.threshold,
        min_ask_size_usd=args.min_ask_size_usd,
        fast_settling_hours=args.hours,
        poll_seconds=args.poll,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
