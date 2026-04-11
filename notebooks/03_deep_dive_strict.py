#!/usr/bin/env python3
"""
Phase 1A — Full-history pull for the 17 strict-tier survivors.

Reads the strict-tier wallet list from data/polymarket_step2_final_summary.json,
pulls each wallet's complete trade history via timestamp pagination (no cap),
fetches market metadata for every unique condition_id, and saves everything
to SQLite for subsequent deep-dive analysis.

Separate database from the Step 2 stats DB:
    data/polymarket_strict_deep.db

Tables:
    strict_wallets    — one row per survivor with headline stats from Step 2
    strict_trades     — one row per raw trade (all 17 wallets combined)
    strict_markets    — one row per unique market metadata record

Re-runnable: uses INSERT OR REPLACE so pagination interruptions don't corrupt data.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

# Reuse the client from 02_characterization.py.
# File has a leading digit so `import` won't work directly — load via importlib.util.
import importlib.util
_CHAR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "02_characterization.py")
_spec = importlib.util.spec_from_file_location("step2_characterization", _CHAR_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
PolyClient = _mod.PolyClient

log = logging.getLogger("phase1a")

DEFAULT_RATE = 10.0
GAMMA_BATCH_SIZE = 50


# ------------------------------ db schema ------------------------------ #

SCHEMA = """
CREATE TABLE IF NOT EXISTS strict_wallets (
    wallet TEXT PRIMARY KEY,
    name TEXT,
    source_run TEXT,
    step2_total_trades INTEGER,
    step2_fast_settling INTEGER,
    step2_realized_pnl REAL,
    step2_win_rate REAL,
    step2_bootstrap_pvalue REAL,
    step2_counted_cats TEXT,
    full_trades_pulled INTEGER,
    pulled_at INTEGER
);

CREATE TABLE IF NOT EXISTS strict_trades (
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

CREATE INDEX IF NOT EXISTS idx_strict_trades_wallet_ts ON strict_trades(wallet, timestamp);
CREATE INDEX IF NOT EXISTS idx_strict_trades_cid ON strict_trades(condition_id);

CREATE TABLE IF NOT EXISTS strict_markets (
    condition_id TEXT PRIMARY KEY,
    question TEXT,
    slug TEXT,
    event_slug TEXT,
    category TEXT,
    start_date TEXT,
    end_date TEXT,
    closed INTEGER,
    outcome_prices TEXT,
    resolve_ts INTEGER,
    fetched_at INTEGER
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ------------------------------ helpers ------------------------------ #

def parse_resolve_ts(meta: dict) -> int | None:
    for k in ("endDate", "endDateIso", "end_date_iso"):
        v = meta.get(k)
        if not v:
            continue
        try:
            return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
        except Exception:
            continue
    return None


def save_trades(conn, wallet: str, trades: list) -> int:
    rows = []
    for t in trades:
        tx = t.get("transactionHash")
        if not tx:
            continue
        cid = t.get("conditionId")
        if not cid:
            continue
        rows.append((
            tx,
            wallet,
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
        """INSERT OR REPLACE INTO strict_trades
           (tx_hash, wallet, timestamp, condition_id, asset, side,
            outcome_index, outcome, price, size, usdc_size, title, slug, event_slug)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows
    )
    conn.commit()
    return len(rows)


def save_markets(conn, meta_by_cid: dict):
    now = int(time.time())
    rows = []
    for cid, m in meta_by_cid.items():
        if not m:
            continue
        category = (
            m.get("category")
            or m.get("groupItemTitle")
            or ""
        )
        outcome_prices = m.get("outcomePrices")
        if outcome_prices is not None and not isinstance(outcome_prices, str):
            outcome_prices = json.dumps(outcome_prices)
        rows.append((
            cid,
            (m.get("question") or "")[:300],
            (m.get("slug") or "")[:200],
            (m.get("eventSlug") or "")[:200],
            category[:50],
            str(m.get("startDate") or m.get("startDateIso") or ""),
            str(m.get("endDate") or m.get("endDateIso") or ""),
            1 if m.get("closed") else 0,
            outcome_prices,
            parse_resolve_ts(m),
            now,
        ))
    if rows:
        conn.executemany(
            """INSERT OR REPLACE INTO strict_markets
               (condition_id, question, slug, event_slug, category,
                start_date, end_date, closed, outcome_prices, resolve_ts, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()


# ------------------------------ main ------------------------------ #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step2-summary", default="data/polymarket_step2_final_summary.json")
    parser.add_argument("--db", default="data/polymarket_strict_deep.db")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE)
    parser.add_argument("--max-pages-per-wallet", type=int, default=2000,
                        help="Hard safety cap (2000 pages = 1M trades — effectively unlimited)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    t_start = time.time()
    client = PolyClient(rate=args.rate)

    # Load strict survivors from Step 2 summary
    with open(args.step2_summary) as f:
        step2 = json.load(f)

    strict = [t for t in step2["traders"] if t.get("passes_strict")]
    strict = sorted(strict, key=lambda t: -(t.get("total_realized_pnl") or 0))
    log.info("=" * 70)
    log.info("PHASE 1A — full-history pull for %d strict-tier survivors", len(strict))
    log.info("source: %s", args.step2_summary)
    log.info("db:     %s", args.db)
    log.info("rate:   %.1f req/s", args.rate)
    log.info("=" * 70)

    conn = init_db(args.db)

    # Stage 1: pull full history for each wallet
    log.info("[Stage 1] pulling full history per wallet")
    total_trades_pulled = 0
    per_wallet_counts = {}
    for i, s in enumerate(strict):
        wallet = s["wallet"]
        name = s["name"] or wallet[:12]
        t0 = time.time()
        trades = client.all_activity(wallet, max_pages=args.max_pages_per_wallet)
        n_saved = save_trades(conn, wallet, trades)
        per_wallet_counts[wallet] = len(trades)
        total_trades_pulled += len(trades)
        log.info("  [%2d/%d] %-26s %6d trades (saved %d) in %.1fs  cum_req=%d",
                 i + 1, len(strict),
                 name[:26],
                 len(trades), n_saved,
                 time.time() - t0,
                 client.stats["requests"])

        # Upsert wallet row
        conn.execute(
            """INSERT OR REPLACE INTO strict_wallets
               (wallet, name, source_run, step2_total_trades, step2_fast_settling,
                step2_realized_pnl, step2_win_rate, step2_bootstrap_pvalue,
                step2_counted_cats, full_trades_pulled, pulled_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (wallet, name, "step2_final",
             s.get("total_trades", 0),
             s.get("fast_settling_trade_count", 0),
             s.get("total_realized_pnl", 0),
             s.get("win_rate"),
             s.get("bootstrap_pvalue"),
             json.dumps(s.get("counted_cats_strict", [])),
             len(trades),
             int(time.time())),
        )
        conn.commit()

    log.info("  → total raw trades pulled: %d", total_trades_pulled)

    # Stage 2: metadata cache for every unique condition_id
    log.info("[Stage 2] metadata cache")
    all_cids = [row[0] for row in conn.execute("SELECT DISTINCT condition_id FROM strict_trades")]
    log.info("  unique condition_ids to fetch: %d", len(all_cids))

    meta_by_cid = client.markets_by_cids(all_cids)
    save_markets(conn, meta_by_cid)

    hit_rate = 100.0 * len(meta_by_cid) / max(len(all_cids), 1)
    log.info("  metadata hit: %d/%d (%.1f%%)", len(meta_by_cid), len(all_cids), hit_rate)

    # Stage 3: summary report
    elapsed = time.time() - t_start
    log.info("=" * 70)
    log.info("PHASE 1A COMPLETE")
    log.info("=" * 70)
    log.info("  elapsed:          %.0fs (%.1f min)", elapsed, elapsed / 60)
    log.info("  api requests:     %d", client.stats["requests"])
    log.info("  rate-limit hits:  %d", client.stats["429s"])
    log.info("  errors:           %d", client.stats["errors"])
    log.info("  total trades:     %d", total_trades_pulled)
    log.info("  unique markets:   %d", len(all_cids))
    log.info("  db:               %s", args.db)

    log.info("\n  Per-wallet trade counts:")
    for s in strict:
        w = s["wallet"]
        step2_n = s.get("total_trades", 0)
        full_n = per_wallet_counts.get(w, 0)
        delta = full_n - step2_n
        marker = " (+)" if delta > 100 else ""
        log.info("    %-26s step2=%6d  full=%6d  delta=%+6d%s",
                 s["name"][:26], step2_n, full_n, delta, marker)

    return 0


if __name__ == "__main__":
    sys.exit(main())
