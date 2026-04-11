#!/usr/bin/env python3
"""
Track 2B — Pull market price history from CLOB for every trade's entry market.

For each unique (condition_id, token_id) pair across the specified wallet set,
call CLOB /prices-history and save the full time series to SQLite. Then we can
do in-memory lookups of "what was the market showing at THIS timestamp?" for
slippage analysis, trader-vs-market Brier, and early/late classification.

Uses YES-side tokens only for binary markets (NO price ≈ 1 - YES price, close
enough for our calibration analysis). Halves the API cost vs pulling both sides.

Scopes:
  --priority-only    just the 4 priority traders (~5-10 min)
  --strict-all       all 17 original strict wallets (~1-2 hrs)
  --expanded         all 17 strict + named honorable candidates (~3-4 hrs)

Output:
  strict_price_history table in data/polymarket_strict_deep.db
  (condition_id, token_id, ts, price)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from collections import defaultdict

import requests

# Reuse PolyClient for rate limiting + retries
import importlib.util
_CHAR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "02_characterization.py")
_spec = importlib.util.spec_from_file_location("step2_characterization", _CHAR_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
PolyClient = _mod.PolyClient

CLOB_API = "https://clob.polymarket.com"

PRIORITY_NAMES = {
    "0x006cc834cc092684f1b56626e23bedb3835c16ea",  # 0x006cc834Cc… discretionary
    "0x161eb16874",  # piston777 NBA scalper (wallet prefix match)
    "0x8c80d213c0",  # SecondWindCapital tennis underdog
    "0xd1c769317b",  # 033033033 near-cert arb
}

log = logging.getLogger("price_history")


def init_price_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strict_price_history (
            token_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            ts INTEGER NOT NULL,
            price REAL NOT NULL,
            PRIMARY KEY (token_id, ts)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_hist_cid_ts
        ON strict_price_history(condition_id, ts)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history_fetched (
            token_id TEXT PRIMARY KEY,
            condition_id TEXT NOT NULL,
            status TEXT NOT NULL,
            n_points INTEGER,
            fetched_at INTEGER NOT NULL
        )
    """)
    conn.commit()


def get_wallet_filter(conn, scope: str, priority_only: bool) -> list:
    """Return list of wallet addresses to include based on scope."""
    if priority_only:
        rows = conn.execute("SELECT wallet, name FROM strict_wallets").fetchall()
        matches = []
        for w, n in rows:
            if "006cc834cc" in w.lower() or "piston777" in (n or "") \
               or "secondwindcapital" in (n or "").lower() or "033033033" in (n or ""):
                matches.append(w)
        return matches
    elif scope == "strict":
        rows = conn.execute("SELECT wallet FROM strict_wallets").fetchall()
        return [r[0] for r in rows]
    elif scope == "all":
        # Both strict + honorable
        rows = conn.execute("SELECT wallet FROM strict_wallets").fetchall()
        strict_wallets = [r[0] for r in rows]
        rows = conn.execute("SELECT wallet FROM honorable_wallets").fetchall()
        honorable_wallets = [r[0] for r in rows]
        return list(set(strict_wallets + honorable_wallets))
    return []


def get_tokens_for_wallets(conn, wallets: list) -> list:
    """
    For the given wallets, find every unique (condition_id, token_id, outcome_index)
    they traded across BOTH strict_trades and honorable_trades. Paired with the
    window: [earliest_trade - 1d, resolve_ts + 1d].

    CLOB /prices-history caps window at ~15 days — fine because fast-settling
    markets all have lifetimes well under that.
    """
    if not wallets:
        return []
    placeholders = ",".join("?" * len(wallets))
    # Union over both strict and honorable trade tables
    rows = conn.execute(f"""
        WITH combined AS (
            SELECT condition_id, asset, outcome_index, timestamp
            FROM strict_trades
            WHERE wallet IN ({placeholders})
              AND asset IS NOT NULL AND asset != ''
            UNION ALL
            SELECT condition_id, asset, outcome_index, timestamp
            FROM honorable_trades
            WHERE wallet IN ({placeholders})
              AND asset IS NOT NULL AND asset != ''
        )
        SELECT c.condition_id, c.asset, c.outcome_index,
               MIN(c.timestamp) AS first_ts, MAX(c.timestamp) AS last_ts,
               m.resolve_ts
        FROM combined c
        LEFT JOIN strict_markets m ON c.condition_id = m.condition_id
        GROUP BY c.condition_id, c.asset, c.outcome_index
    """, wallets + wallets).fetchall()

    import time as _t
    now = int(_t.time())
    results = []
    seen_cid_oi = set()
    for cid, token, oi, first_ts, last_ts, resolve_ts in rows:
        key = (cid, oi)
        if key in seen_cid_oi:
            continue
        seen_cid_oi.add(key)

        # Compute window: 1 day before earliest trade to 1 day after resolve/last trade.
        window_start = int(first_ts - 86400)
        if window_start < 0:
            window_start = 0
        window_end_raw = int((resolve_ts or last_ts or now) + 86400)
        # CLOB caps window at ~15 days
        MAX_WINDOW_S = 15 * 86400
        if window_end_raw - window_start > MAX_WINDOW_S:
            # Center the 15-day window around the trade activity
            mid = (first_ts + last_ts) // 2
            window_start = mid - MAX_WINDOW_S // 2
            window_end_raw = mid + MAX_WINDOW_S // 2
        results.append((cid, token, oi, window_start, window_end_raw))
    return results


def fetch_price_history(client, token_id: str, start_ts: int, end_ts: int):
    """
    CLOB /prices-history behavior (discovered empirically 2026-04-11):
      - `interval=max` works only for *open* markets; silently returns empty for resolved.
      - `startTs`/`endTs` works for BOTH open and resolved markets, but the window
        is capped at ~15 days. 0→now returns HTTP 400.
    Always use a narrow window tight around the market's actual lifetime.
    """
    r = client._get(
        f"{CLOB_API}/prices-history",
        params={"market": token_id, "startTs": start_ts, "endTs": end_ts, "fidelity": 1},
    )
    if r is None:
        return None
    if isinstance(r, dict):
        return r.get("history", [])
    if isinstance(r, list):
        return r
    return None


def save_price_points(conn, token_id: str, cid: str, points: list) -> int:
    if not points:
        return 0
    rows = []
    for p in points:
        ts = p.get("t")
        price = p.get("p")
        if ts is None or price is None:
            continue
        try:
            rows.append((token_id, cid, int(ts), float(price)))
        except (ValueError, TypeError):
            continue
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO strict_price_history (token_id, condition_id, ts, price) VALUES (?,?,?,?)",
            rows,
        )
        conn.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/polymarket_strict_deep.db")
    parser.add_argument("--scope", choices=["priority", "strict", "expanded", "all"], default="priority")
    parser.add_argument("--rate", type=float, default=10.0)
    parser.add_argument("--skip-fetched", action="store_true",
                        help="Skip tokens already in price_history_fetched table")
    parser.add_argument("--progress-every", type=int, default=100)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    conn = sqlite3.connect(args.db)
    init_price_table(conn)

    priority_only = args.scope == "priority"
    wallets = get_wallet_filter(conn, "strict", priority_only)
    log.info("[price_history] scope=%s  wallets=%d", args.scope, len(wallets))
    for w in wallets:
        row = conn.execute("SELECT name FROM strict_wallets WHERE wallet = ?", (w,)).fetchone()
        log.info("  - %s  %s", w[:14], row[0] if row else "?")

    tokens = get_tokens_for_wallets(conn, wallets)
    log.info("[price_history] unique (cid, token, outcome) to pull: %d", len(tokens))

    if args.skip_fetched:
        already = set(r[0] for r in conn.execute(
            "SELECT token_id FROM price_history_fetched WHERE status='ok'"
        ).fetchall())
        tokens = [x for x in tokens if x[1] not in already]
        log.info("[price_history] after skip-fetched: %d remaining", len(tokens))

    client = PolyClient(rate=args.rate)
    t0 = time.time()

    n_ok = 0
    n_empty = 0
    n_err = 0
    total_points = 0
    for i, (cid, token, oi, w_start, w_end) in enumerate(tokens, 1):
        history = fetch_price_history(client, token, w_start, w_end)
        if history is None:
            status = "error"
            n_err += 1
        elif not history:
            status = "empty"
            n_empty += 1
        else:
            saved = save_price_points(conn, token, cid, history)
            total_points += saved
            status = "ok"
            n_ok += 1

        conn.execute(
            """INSERT OR REPLACE INTO price_history_fetched
               (token_id, condition_id, status, n_points, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token, cid, status, len(history) if history else 0, int(time.time())),
        )
        conn.commit()

        if i % args.progress_every == 0 or i == len(tokens):
            rate_actual = client.stats["requests"] / max(time.time() - t0, 1)
            log.info("  %d/%d ok=%d empty=%d err=%d points=%d elapsed=%.0fs (%.1f req/s)",
                     i, len(tokens), n_ok, n_empty, n_err, total_points,
                     time.time() - t0, rate_actual)

    log.info("\n[price_history] complete: ok=%d empty=%d err=%d points=%d  elapsed=%.0fs",
             n_ok, n_empty, n_err, total_points, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
