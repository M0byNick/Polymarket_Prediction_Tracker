#!/usr/bin/env python3
"""Stage 4 paper trader — consumes paper_signals, writes paper_orders + paper_positions.

Fill model (MVP)
----------------
**Taker-at-ask.** For every ``paper_signals`` row with
``status='new'``, the trader simulates an instant taker fill at the
recorded ``trigger_price`` (which was the best ask at signal time).
Qty is the larger of:

- ``ask_size`` shares available at that price, and
- ``flat_usd / trigger_price`` shares — whichever is **smaller**, so
  we never exceed either the book depth or the per-position cap.

``flat_usd`` defaults to $100 per the Stage 4 roadmap. The resulting
fill writes one ``paper_orders`` row (``status='filled'``) and one
``paper_positions`` row (``status='open'``). The signal is marked
``'consumed'``.

**Deliberate conservatism.** Real passive fills on the NO side would
likely be cheaper (the trader whose edge we're copying got 82% of
fills below mid), so taker-at-ask is a pessimistic bound on paper
returns. If paper performance with this model is acceptable, live
performance with passive orders should be at least as good — assuming
the edge generalizes.

Skipped for MVP
---------------
- Partial fills (we assume ask_size >= desired qty post-filter).
- Order cancellation / modification.
- Price-impact model beyond the single best-ask level.
- Walking the book for size larger than best-ask depth.

Cadence
-------
5 s sleep between passes. Event-driven would be cleaner but a 5 s
latency between signal and paper fill is noise compared to Euro soccer
pre-match price movement.

Env vars
--------
PAPER_FLAT_USD      Default 100. Dollar size per position.
PAPER_DB_PATH       Default data/polymarket_snapshots.db.
PAPER_POLL_SECONDS  Default 5.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.db import connect  # noqa: E402
from src.log import get_logger  # noqa: E402
from src import paper_schema  # noqa: E402,F401  (registers DDL)

log = get_logger("paper_trader")

DEFAULT_DB_PATH = "data/polymarket_snapshots.db"
DEFAULT_FLAT_USD = 100.0
DEFAULT_POLL_SECONDS = 5


def _now_unix() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def consume_pending_signals(db_path: str, flat_usd: float) -> int:
    """Read new signals, simulate fills, write orders + positions.

    Returns the count of signals consumed this pass. All writes for a
    given signal are wrapped in a single SQLite transaction so a crash
    mid-flow doesn't leave dangling orders without positions.
    """
    consumed = 0
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT id, condition_id, token_id, side, trigger_price,
                   ask_size, resolve_ts
            FROM paper_signals
            WHERE status = 'new'
            ORDER BY created_ts ASC
            """
        )
        rows = cur.fetchall()
        if not rows:
            return 0

        for row in rows:
            signal_id = row["id"]
            cid = row["condition_id"]
            token_id = row["token_id"]
            side = row["side"]
            trigger_price = row["trigger_price"]
            ask_size = row["ask_size"] or 0.0
            resolve_ts = row["resolve_ts"]

            # Sanity: trigger price must be > 0 for the PnL math below.
            if trigger_price is None or trigger_price <= 0 or trigger_price >= 1:
                conn.execute(
                    "UPDATE paper_signals SET status='rejected', "
                    "reject_reason=?, consumed_ts=? WHERE id=?",
                    ("invalid_trigger_price", _now_unix(), signal_id),
                )
                continue

            # Sizing: flat_usd notional, but cap at available ask_size.
            desired_shares = flat_usd / trigger_price
            fill_shares = min(desired_shares, ask_size) if ask_size > 0 else desired_shares
            fill_usd = fill_shares * trigger_price

            if fill_shares < 1.0:
                # Below 1 share = < $trigger_price of exposure. Skip.
                conn.execute(
                    "UPDATE paper_signals SET status='rejected', "
                    "reject_reason=?, consumed_ts=? WHERE id=?",
                    ("insufficient_size", _now_unix(), signal_id),
                )
                continue

            now = _now_unix()
            # paper_orders (submitted + filled in one go for MVP)
            order_cur = conn.execute(
                """
                INSERT INTO paper_orders (
                    signal_id, condition_id, token_id, side,
                    limit_price, qty_shares, qty_usd, status,
                    fill_price, fill_ts, submitted_ts
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    signal_id, cid, token_id, side,
                    trigger_price, fill_shares, fill_usd, "filled",
                    trigger_price, now, now,
                ),
            )
            order_id = order_cur.lastrowid

            # paper_positions
            conn.execute(
                """
                INSERT INTO paper_positions (
                    order_id, condition_id, token_id, side,
                    fill_price, qty_shares, qty_usd, fill_ts,
                    resolve_ts, status
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    order_id, cid, token_id, side,
                    trigger_price, fill_shares, fill_usd, now,
                    resolve_ts, "open",
                ),
            )

            conn.execute(
                "UPDATE paper_signals SET status='consumed', consumed_ts=? WHERE id=?",
                (now, signal_id),
            )
            consumed += 1

            log.info(
                "FILL cid=%s side=%s price=%.4f shares=%.1f usd=%.2f "
                "(sig_id=%d order_id=%d)",
                cid[:10], side, trigger_price, fill_shares, fill_usd,
                signal_id, order_id,
            )

        conn.commit()

    return consumed


def loop(
    db_path: str = DEFAULT_DB_PATH,
    flat_usd: float = DEFAULT_FLAT_USD,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
) -> None:
    log.info(
        "paper_trader starting: db=%s flat_usd=$%.0f poll=%ds",
        db_path, flat_usd, poll_seconds,
    )
    while True:
        try:
            n = consume_pending_signals(db_path, flat_usd)
            if n:
                log.info("consumed %d signal(s)", n)
        except Exception as ex:  # noqa: BLE001
            log.exception("consume failed: %s", ex)
        time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("PAPER_DB_PATH", DEFAULT_DB_PATH))
    parser.add_argument(
        "--flat-usd",
        type=float,
        default=float(os.environ.get("PAPER_FLAT_USD", DEFAULT_FLAT_USD)),
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=int(os.environ.get("PAPER_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Drain pending signals once and exit (for smoke testing).",
    )
    args = parser.parse_args()

    if args.once:
        n = consume_pending_signals(args.db, args.flat_usd)
        log.info("one-shot consumed %d signal(s)", n)
        return 0

    loop(db_path=args.db, flat_usd=args.flat_usd, poll_seconds=args.poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
