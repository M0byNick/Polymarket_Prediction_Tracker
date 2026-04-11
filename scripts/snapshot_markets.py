#!/usr/bin/env python3
"""
Forward snapshotting — Polymarket fast-settling market price archive.

Polls Polymarket for all currently-open markets that will resolve within
FAST_SETTLING_HOURS, fetches mid-price + order book top-of-book, and writes
a timestamped snapshot row to SQLite. Designed to run as a cron job every
SNAPSHOT_INTERVAL_MINUTES.

Why: we need a forward-logged time series of market prices at arbitrary
timestamps so that when we later analyze trader entries (or our own model's
signals) we can say what the market was showing at that instant. The
Polymarket CLOB prices-history endpoint covers this for resolved markets,
but running a forward archive gives us (a) finer granularity, (b) order
book depth, (c) certainty that data exists for the markets we care about.

Output: data/polymarket_snapshots.db

Cron entry (run every 10 min):
    */10 * * * * cd <repo> && python3 scripts/snapshot_markets.py >> data/snapshot.log 2>&1
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

DEFAULT_INTERVAL_MIN = 10
FAST_SETTLING_HOURS = 72
DEFAULT_RATE = 10.0
TIMEOUT = 15

# Liquidity filter — a market qualifies for snapshotting only if its
# 24h volume OR current liquidity exceeds this threshold. Filters out
# ~80% of markets (long-tail illiquid) while keeping anything someone
# has actively traded.
MIN_VOLUME_24H_USD = 500.0
MIN_LIQUIDITY_USD = 500.0

log = logging.getLogger("snapshot")


# ------------------------------ db schema ------------------------------ #

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot_markets (
    condition_id TEXT PRIMARY KEY,
    question TEXT,
    slug TEXT,
    category TEXT,
    start_date TEXT,
    end_date TEXT,
    resolve_ts INTEGER,
    clob_token_yes TEXT,
    clob_token_no TEXT,
    first_seen_ts INTEGER NOT NULL,
    last_seen_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_prices (
    condition_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    token_id TEXT NOT NULL,
    mid_price REAL,
    best_bid REAL,
    best_ask REAL,
    bid_size REAL,
    ask_size REAL,
    spread REAL,
    PRIMARY KEY (condition_id, ts, token_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_prices_cid_ts
    ON snapshot_prices(condition_id, ts);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ------------------------------ rate limiter ------------------------------ #

class RateLimiter:
    def __init__(self, rate: float):
        self.min_interval = 1.0 / rate
        self.last = 0.0

    def wait(self):
        now = time.monotonic()
        d = self.last + self.min_interval - now
        if d > 0:
            time.sleep(d)
        self.last = time.monotonic()


# ------------------------------ fetch ------------------------------ #

def fetch_open_fast_settling_markets(rl: RateLimiter, hours: int,
                                      min_vol: float, min_liq: float) -> list:
    """
    Query Gamma for all currently-open markets. Filter to those whose
    resolve_ts is within `hours` of now AND meet liquidity thresholds.
    """
    now = int(time.time())
    cutoff = now + hours * 3600

    markets = []
    offset = 0
    page_size = 500
    n_checked = 0
    n_too_far = 0
    n_illiquid = 0
    while True:
        rl.wait()
        try:
            r = requests.get(
                f"{GAMMA_API}/markets",
                params={
                    "closed": "false",
                    "active": "true",
                    "limit": page_size,
                    "offset": offset,
                },
                timeout=TIMEOUT,
            )
            if r.status_code != 200:
                log.warning("gamma %s at offset=%d", r.status_code, offset)
                break
            batch = r.json()
        except Exception as ex:
            log.warning("gamma exception at offset=%d: %s", offset, ex)
            break
        if not isinstance(batch, list) or not batch:
            break

        for m in batch:
            n_checked += 1
            end_date = m.get("endDate") or m.get("endDateIso")
            if not end_date:
                continue
            try:
                resolve_ts = int(datetime.fromisoformat(
                    str(end_date).replace("Z", "+00:00")
                ).timestamp())
            except Exception:
                continue
            if resolve_ts <= now or resolve_ts > cutoff:
                n_too_far += 1
                continue

            # Liquidity filter
            try:
                vol24 = float(m.get("volume24hr") or 0)
            except (ValueError, TypeError):
                vol24 = 0.0
            try:
                liq = float(m.get("liquidityClob") or m.get("liquidity") or 0)
            except (ValueError, TypeError):
                liq = 0.0
            if vol24 < min_vol and liq < min_liq:
                n_illiquid += 1
                continue

            markets.append((m, resolve_ts))

        if len(batch) < page_size:
            break
        offset += page_size

    log.info("[snapshot] scan: checked=%d  too_far=%d  illiquid=%d  kept=%d",
             n_checked, n_too_far, n_illiquid, len(markets))
    return markets


def fetch_book_for_token(rl: RateLimiter, token_id: str) -> dict | None:
    rl.wait()
    try:
        r = requests.get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def top_of_book(book: dict) -> tuple[float | None, float | None, float | None, float | None]:
    """
    Return (best_bid_price, best_bid_size, best_ask_price, best_ask_size).

    Polymarket CLOB book format: { bids: [{price, size}, ...], asks: [...] }
    Bids sorted descending by price, asks ascending. "best" is index 0 of each.
    """
    if not book:
        return None, None, None, None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    best_bid_p = float(bids[0]["price"]) if bids else None
    best_bid_s = float(bids[0]["size"]) if bids else None
    best_ask_p = float(asks[0]["price"]) if asks else None
    best_ask_s = float(asks[0]["size"]) if asks else None
    return best_bid_p, best_bid_s, best_ask_p, best_ask_s


# ------------------------------ main ------------------------------ #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/polymarket_snapshots.db")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE)
    parser.add_argument("--max-markets", type=int, default=0,
                        help="Cap on how many markets to snapshot per run (0 = no cap)")
    parser.add_argument("--hours", type=int, default=FAST_SETTLING_HOURS,
                        help="Only snapshot markets resolving within this many hours")
    parser.add_argument("--min-vol-24h", type=float, default=MIN_VOLUME_24H_USD,
                        help="Skip markets with 24h volume below this (and low liq)")
    parser.add_argument("--min-liq", type=float, default=MIN_LIQUIDITY_USD,
                        help="Skip markets with CLOB liquidity below this (and low vol)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    t0 = time.time()
    ts_now = int(t0)
    rl = RateLimiter(args.rate)
    conn = init_db(args.db)

    log.info("[snapshot] fetching open fast-settling markets from Gamma")
    markets = fetch_open_fast_settling_markets(rl, args.hours, args.min_vol_24h, args.min_liq)
    log.info("[snapshot] %d open markets resolve within %dh (after liquidity filter)",
             len(markets), args.hours)
    if args.max_markets > 0:
        markets = markets[:args.max_markets]
        log.info("[snapshot] capped to %d for this run", len(markets))

    # Upsert markets metadata
    market_rows = []
    market_tokens = {}  # cid -> [token_id, ...]
    for m, resolve_ts in markets:
        cid = m.get("conditionId")
        if not cid:
            continue
        tokens = m.get("clobTokenIds")
        if isinstance(tokens, str):
            import json
            try:
                tokens = json.loads(tokens)
            except Exception:
                tokens = []
        if not tokens or not isinstance(tokens, list):
            continue
        token_yes = tokens[0] if len(tokens) > 0 else None
        token_no = tokens[1] if len(tokens) > 1 else None
        market_rows.append((
            cid,
            (m.get("question") or "")[:300],
            (m.get("slug") or "")[:200],
            (m.get("category") or m.get("groupItemTitle") or "")[:50],
            str(m.get("startDate") or m.get("startDateIso") or ""),
            str(m.get("endDate") or m.get("endDateIso") or ""),
            resolve_ts,
            token_yes,
            token_no,
            ts_now,
            ts_now,
        ))
        market_tokens[cid] = [t for t in (token_yes, token_no) if t]

    for row in market_rows:
        conn.execute(
            """INSERT INTO snapshot_markets
               (condition_id, question, slug, category, start_date, end_date,
                resolve_ts, clob_token_yes, clob_token_no, first_seen_ts, last_seen_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(condition_id) DO UPDATE SET
                 last_seen_ts = excluded.last_seen_ts""",
            row,
        )
    conn.commit()

    log.info("[snapshot] fetching order book for %d markets", len(market_tokens))
    price_rows = []
    n_fetched = 0
    for cid, tokens in market_tokens.items():
        for token_id in tokens:
            book = fetch_book_for_token(rl, token_id)
            if not book:
                continue
            bid_p, bid_s, ask_p, ask_s = top_of_book(book)
            if bid_p is None and ask_p is None:
                continue
            mid = None
            if bid_p is not None and ask_p is not None:
                mid = (bid_p + ask_p) / 2
            elif bid_p is not None:
                mid = bid_p
            elif ask_p is not None:
                mid = ask_p
            spread = (ask_p - bid_p) if (bid_p is not None and ask_p is not None) else None
            price_rows.append((cid, ts_now, token_id, mid, bid_p, ask_p, bid_s, ask_s, spread))
            n_fetched += 1

    if price_rows:
        conn.executemany(
            """INSERT OR REPLACE INTO snapshot_prices
               (condition_id, ts, token_id, mid_price, best_bid, best_ask,
                bid_size, ask_size, spread)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            price_rows,
        )
        conn.commit()

    log.info("[snapshot] wrote %d price rows across %d markets in %.1fs",
             len(price_rows), len(market_tokens), time.time() - t0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
