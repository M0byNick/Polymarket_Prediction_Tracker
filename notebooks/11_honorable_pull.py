#!/usr/bin/env python3
"""
Phase 3b — Full-history pull for top-N honorable-tier candidates.

Reads the top-ranked honorable candidates from Phase 1E's promotion score,
pulls their complete trade history, and stores in the same schema as the
strict_deep database but under new tables (honorable_wallets, honorable_trades).

Uses the same PolyClient / pagination / metadata-cache pattern as Phase 1A.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

_CHAR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "02_characterization.py")
_spec = importlib.util.spec_from_file_location("step2_characterization", _CHAR_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
PolyClient = _mod.PolyClient

log = logging.getLogger("phase3b")


SCHEMA = """
CREATE TABLE IF NOT EXISTS honorable_wallets (
    wallet TEXT PRIMARY KEY,
    name TEXT,
    promote_score REAL,
    step2_total_trades INTEGER,
    step2_fast_settling INTEGER,
    step2_realized_pnl REAL,
    step2_win_rate REAL,
    step2_bootstrap_pvalue REAL,
    full_trades_pulled INTEGER,
    pulled_at INTEGER
);

CREATE TABLE IF NOT EXISTS honorable_trades (
    tx_hash TEXT,
    wallet TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    condition_id TEXT NOT NULL,
    asset TEXT,
    side TEXT,
    outcome_index INTEGER,
    outcome TEXT,
    price REAL,
    size REAL,
    usdc_size REAL,
    title TEXT,
    slug TEXT,
    event_slug TEXT,
    PRIMARY KEY (wallet, tx_hash, condition_id, outcome_index)
);

CREATE INDEX IF NOT EXISTS idx_hon_trades_wallet_ts ON honorable_trades(wallet, timestamp);
CREATE INDEX IF NOT EXISTS idx_hon_trades_cid ON honorable_trades(condition_id);

-- Share the same strict_markets table with strict pull — markets overlap
-- so we don't want duplicate metadata.
"""


def promote_score(t: dict) -> float:
    pnl = t.get("total_realized_pnl") or 0
    if pnl <= 0:
        return -1
    pv = t.get("bootstrap_pvalue")
    if pv is None:
        pv_score = 0.5
    elif pv < 0.1:
        pv_score = 1.0
    elif pv < 0.2:
        pv_score = 0.5
    else:
        pv_score = 0.2
    fs = t.get("fast_settling_trade_count") or 0
    sample_score = min(1.0, fs / 1000)
    return pnl * pv_score * sample_score


def init_db(db_path: str):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def save_trades(conn, wallet: str, trades: list) -> int:
    rows = []
    for t in trades:
        tx = t.get("transactionHash")
        cid = t.get("conditionId")
        if not (tx and cid):
            continue
        rows.append((
            tx, wallet,
            int(t.get("timestamp", 0) or 0),
            cid,
            t.get("asset", ""),
            t.get("side", ""),
            int(t.get("outcomeIndex") or 0),
            t.get("outcome", ""),
            float(t.get("price", 0) or 0),
            float(t.get("size", 0) or 0),
            float(t.get("usdcSize", 0) or 0),
            (t.get("title") or "")[:200],
            (t.get("slug") or "")[:200],
            (t.get("eventSlug") or "")[:200],
        ))
    if not rows:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO honorable_trades
           (tx_hash, wallet, timestamp, condition_id, asset, side,
            outcome_index, outcome, price, size, usdc_size, title, slug, event_slug)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step2-summary", default="data/polymarket_step2_final_summary.json")
    parser.add_argument("--db", default="data/polymarket_strict_deep.db",
                        help="Shared DB with strict tables")
    parser.add_argument("--top-n", type=int, default=25)
    parser.add_argument("--rate", type=float, default=10.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Load honorable-only candidates and rank by promote_score
    with open(args.step2_summary) as f:
        data = json.load(f)

    honorable_only = [
        t for t in data["traders"]
        if t["passes_honorable"] and not t["passes_strict"]
    ]
    scored = [(promote_score(t), t) for t in honorable_only]
    scored.sort(key=lambda x: -x[0])
    top_n = scored[:args.top_n]

    client = PolyClient(rate=args.rate)
    conn = init_db(args.db)

    t_start = time.time()
    log.info("=" * 70)
    log.info("PHASE 3B — honorable full-history pull (top %d)", args.top_n)
    log.info("=" * 70)

    total_trades = 0
    for i, (score, t) in enumerate(top_n):
        wallet = t["wallet"]
        name = t["name"] or wallet[:12]

        t0 = time.time()
        trades = client.all_activity(wallet, max_pages=500)
        n_saved = save_trades(conn, wallet, trades)
        total_trades += len(trades)

        conn.execute(
            """INSERT OR REPLACE INTO honorable_wallets
               (wallet, name, promote_score, step2_total_trades, step2_fast_settling,
                step2_realized_pnl, step2_win_rate, step2_bootstrap_pvalue,
                full_trades_pulled, pulled_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (wallet, name, score,
             t.get("total_trades", 0),
             t.get("fast_settling_trade_count", 0),
             t.get("total_realized_pnl", 0),
             t.get("win_rate"),
             t.get("bootstrap_pvalue"),
             len(trades),
             int(time.time())),
        )
        conn.commit()

        log.info("  [%2d/%d] %-28s score=%.0f  %d trades (saved %d) in %.1fs  cum_req=%d",
                 i + 1, len(top_n), name[:28], score,
                 len(trades), n_saved, time.time() - t0,
                 client.stats["requests"])

    log.info("\nPhase 3b complete: %d traders, %d total trades, elapsed=%.0fs  api=%d",
             len(top_n), total_trades, time.time() - t_start, client.stats["requests"])

    # Stage 2: fetch metadata for any new condition IDs not already in strict_markets
    log.info("\n[Stage 2] fetching metadata for new honorable markets")
    all_cids = [r[0] for r in conn.execute(
        "SELECT DISTINCT condition_id FROM honorable_trades"
    ).fetchall()]
    existing = set(r[0] for r in conn.execute(
        "SELECT condition_id FROM strict_markets"
    ).fetchall())
    new_cids = [c for c in all_cids if c not in existing]
    log.info("  %d unique cids in honorable, %d already in strict_markets, %d new",
             len(all_cids), len(existing) & len(all_cids), len(new_cids))

    if new_cids:
        meta_by_cid = client.markets_by_cids(new_cids)
        log.info("  fetched %d / %d (%.1f%%)", len(meta_by_cid), len(new_cids),
                 100 * len(meta_by_cid) / max(len(new_cids), 1))

        # Insert into strict_markets (same schema)
        rows = []
        now = int(time.time())
        for cid, m in meta_by_cid.items():
            outcome_prices = m.get("outcomePrices")
            if outcome_prices is not None and not isinstance(outcome_prices, str):
                outcome_prices = json.dumps(outcome_prices)
            end_date = m.get("endDate") or m.get("endDateIso") or ""
            # Parse resolve_ts
            resolve_ts = None
            if end_date:
                try:
                    resolve_ts = int(datetime.fromisoformat(
                        str(end_date).replace("Z", "+00:00")
                    ).timestamp())
                except Exception:
                    pass
            rows.append((
                cid,
                (m.get("question") or "")[:300],
                (m.get("slug") or "")[:200],
                (m.get("eventSlug") or "")[:200],
                (m.get("groupItemTitle") or m.get("category") or "")[:50],
                str(m.get("startDate") or ""),
                str(end_date),
                1 if m.get("closed") else 0,
                outcome_prices,
                resolve_ts,
                now,
            ))
        conn.executemany(
            """INSERT OR REPLACE INTO strict_markets
               (condition_id, question, slug, event_slug, category,
                start_date, end_date, closed, outcome_prices, resolve_ts, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()

    log.info("[Phase 3b DONE] total elapsed: %.0fs", time.time() - t_start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
