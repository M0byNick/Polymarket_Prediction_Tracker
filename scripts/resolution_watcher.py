#!/usr/bin/env python3
"""Stage 4 resolution watcher — realizes PnL on closed paper positions.

Every 30 min, poll Gamma for markets whose condition_ids correspond to
open paper positions. For any market that Gamma reports as
``closed=true`` with a deterministic binary outcome, compute realized
PnL and update the ``paper_positions`` row:

PnL math (NO-side positions, the only side Stage 4 emits)
---------------------------------------------------------
- Outcome == "NO"  (favorite failed to win → NO wins)::

    realized_pnl = qty_shares * (1 - fill_price)

  Each contract pays $1, we bought at ``fill_price``, so profit per
  contract is ``1 - fill_price``.

- Outcome == "YES" (favorite won → NO loses)::

    realized_pnl = -qty_shares * fill_price

  Each losing contract is worth $0, we bought at ``fill_price``, so
  loss per contract is ``fill_price``.

For YES-side positions (not emitted by Stage 4 MVP but handled
defensively): swap the arithmetic.

Ambiguous resolutions
---------------------
If ``outcomePrices`` is missing or shows a non-binary split (e.g.
``[0.5, 0.5]`` under UMA dispute), the position is left open. It will
be re-checked on the next cycle. Positions that never resolve require
manual intervention (rare; logged as warnings once their ``resolve_ts``
is >7 days past).

Env vars
--------
RESOLUTION_POLL_SECONDS  Default 1800 (30 min).
RESOLUTION_DB_PATH       Default data/polymarket_snapshots.db.
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
from src.polymarket_client import PolymarketClient, parse_resolution  # noqa: E402

log = get_logger("resolution_watcher")

DEFAULT_DB_PATH = "data/polymarket_snapshots.db"
DEFAULT_POLL_SECONDS = 1800


def _now_unix() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def compute_realized_pnl(
    side: str, fill_price: float, qty_shares: float, outcome: str
) -> float:
    """Pure function — exposed for unit testing.

    Contracts pay $1 at resolution to the winning side, $0 to the losing
    side. Paper positions are non-margined so the loss is capped at
    ``qty_shares * fill_price``.
    """
    if side == "NO":
        return (
            qty_shares * (1.0 - fill_price)
            if outcome == "NO"
            else -qty_shares * fill_price
        )
    if side == "YES":
        return (
            qty_shares * (1.0 - fill_price)
            if outcome == "YES"
            else -qty_shares * fill_price
        )
    raise ValueError(f"unknown side: {side}")


def resolve_once(client: PolymarketClient, db_path: str) -> dict:
    """One pass. Returns counters.

    Step 1: pull all ``status='open'`` positions and their condition IDs.
    Step 2: batch-lookup those CIDs via Gamma ``closed=true``.
    Step 3: for any that return with a deterministic outcome, realize
    PnL and mark the position resolved.
    """
    now = _now_unix()
    stats = {
        "open_positions": 0,
        "gamma_hits": 0,
        "resolved": 0,
        "wins": 0,
        "losses": 0,
        "ambiguous": 0,
        "stale": 0,
    }

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, condition_id, side, fill_price, qty_shares, resolve_ts
            FROM paper_positions
            WHERE status = 'open'
            """
        ).fetchall()
    stats["open_positions"] = len(rows)
    if not rows:
        return stats

    # Only poll Gamma for positions whose resolve_ts has passed. If the
    # market hasn't ticked to close yet there's nothing to resolve, and
    # the extra API calls are waste. Accept a 10-minute grace window in
    # case resolve_ts is slightly mis-recorded.
    grace = 600
    due = [r for r in rows if (r["resolve_ts"] or 0) <= (now + grace)]
    if not due:
        return stats

    cids = [r["condition_id"] for r in due]
    markets = client.get_markets_by_condition_ids(cids, closed=True)
    stats["gamma_hits"] = len(markets)
    by_cid = {m.get("conditionId"): m for m in markets if m.get("conditionId")}

    updates: list[tuple] = []
    for row in due:
        cid = row["condition_id"]
        m = by_cid.get(cid)
        if m is None:
            # Not closed yet (Gamma only returned truly-closed rows).
            # Log staleness for positions whose resolve_ts is >7d in the
            # past — those probably need manual intervention.
            if (row["resolve_ts"] or 0) + 7 * 86400 < now:
                stats["stale"] += 1
                log.warning(
                    "position %s (cid=%s) has been open >7d past resolve_ts",
                    row["id"], cid[:10],
                )
            continue

        is_resolved, outcome = parse_resolution(m)
        if not is_resolved or outcome is None:
            stats["ambiguous"] += 1
            continue

        pnl = compute_realized_pnl(
            side=row["side"],
            fill_price=row["fill_price"],
            qty_shares=row["qty_shares"],
            outcome=outcome,
        )
        stats["resolved"] += 1
        if pnl > 0:
            stats["wins"] += 1
        else:
            stats["losses"] += 1
        updates.append((outcome, pnl, now, row["id"]))
        log.info(
            "RESOLVE cid=%s side=%s price=%.4f qty=%.1f outcome=%s pnl=%+.2f",
            cid[:10], row["side"], row["fill_price"], row["qty_shares"],
            outcome, pnl,
        )

    if updates:
        with connect(db_path) as conn:
            conn.executemany(
                """
                UPDATE paper_positions
                SET status='resolved',
                    resolved_outcome=?,
                    realized_pnl=?,
                    resolved_ts=?
                WHERE id=?
                """,
                updates,
            )
            conn.commit()

    return stats


def loop(db_path: str = DEFAULT_DB_PATH, poll_seconds: int = DEFAULT_POLL_SECONDS) -> None:
    client = PolymarketClient()
    log.info(
        "resolution_watcher starting: db=%s poll=%ds",
        db_path, poll_seconds,
    )
    while True:
        t0 = time.monotonic()
        try:
            stats = resolve_once(client, db_path)
            if stats["open_positions"] or stats["resolved"]:
                log.info("cycle done: %s", stats)
        except Exception as ex:  # noqa: BLE001
            log.exception("resolution cycle failed: %s", ex)

        elapsed = time.monotonic() - t0
        sleep_for = max(10.0, poll_seconds - elapsed)
        time.sleep(sleep_for)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=os.environ.get("RESOLUTION_DB_PATH", DEFAULT_DB_PATH)
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=int(os.environ.get("RESOLUTION_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one resolution pass and exit.",
    )
    args = parser.parse_args()

    if args.once:
        client = PolymarketClient()
        stats = resolve_once(client, args.db)
        log.info("one-shot stats: %s", stats)
        return 0

    loop(db_path=args.db, poll_seconds=args.poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
