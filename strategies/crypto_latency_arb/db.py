"""
SQLite schema for the crypto-binary latency arb collector.

Tables:
    markets         — one row per in-scope Polymarket crypto-binary market
    book_snapshots  — per-snapshot top-of-book (bid/ask + sizes) for YES and NO
                      tokens. THIS IS THE CORE TAPE the holdout is scored on.
    cex_spot        — per-snapshot centralized-exchange spot reference, with
                      the source actually used (binance | coinbase) logged.
    model_snapshots — per-snapshot Black-Scholes 0DTE binary model output
                      (convenience column per pre-reg §3; not load-bearing).
    cycles          — per-collection-cycle health summary.

DESIGN NOTES
  - All timestamps are UTC. `ts_ms` columns are integer milliseconds since
    epoch (pre-reg §3.5 requires millisecond precision to later measure the
    Polymarket-vs-CEX divergence half-life). `ts` columns are integer seconds
    (cycle-level granularity).
  - WAL mode + IF NOT EXISTS migrations. Append-only insert, read-mostly
    end-of-holdout analysis from notebooks.
  - OBSERVE-ONLY: there is no orders table, no positions table, no PnL table.
    The collector records raw market state; PnL is computed later, offline,
    from this tape joined to realized resolution outcomes.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    condition_id      TEXT NOT NULL PRIMARY KEY,
    market_slug       TEXT NOT NULL,
    question          TEXT,
    underlying        TEXT NOT NULL,        -- 'BTC' | 'ETH' | 'SOL' | 'XRP'
    family            TEXT NOT NULL,        -- 'updown_5m' | 'updown_15m'
                                            --   | 'updown_hourly' | 'above_hourly'
    horizon           TEXT NOT NULL,        -- 'hourly' | '15min' (pre-reg §2.2 bucket)
    market_kind       TEXT NOT NULL,        -- 'relative' (up vs open) | 'strike' (above $X)
    strike            REAL,                 -- parsed strike for 'strike' markets, else NULL
    resolve_ts        INTEGER NOT NULL,     -- unix seconds, true market resolution time
    start_ts          INTEGER,              -- unix seconds, market creation time
    yes_token         TEXT NOT NULL,        -- CLOB token id for the YES/Up outcome
    no_token          TEXT NOT NULL,        -- CLOB token id for the NO/Down outcome
    resolution_source TEXT,                 -- e.g. 'Binance BTC/USDT', 'Chainlink BTC/USD'
    first_seen_ts     INTEGER NOT NULL,
    last_seen_ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_markets_resolve ON markets(resolve_ts);
CREATE INDEX IF NOT EXISTS idx_markets_underlying ON markets(underlying, horizon);

CREATE TABLE IF NOT EXISTS book_snapshots (
    snapshot_ts_ms    INTEGER NOT NULL,     -- millisecond UTC, collector clock
    condition_id      TEXT NOT NULL,
    yes_bid           REAL,                 -- best bid for the YES token
    yes_ask           REAL,                 -- best ask for the YES token
    yes_bid_size      REAL,
    yes_ask_size      REAL,
    no_bid            REAL,                 -- best bid for the NO token
    no_ask            REAL,
    no_bid_size       REAL,
    no_ask_size       REAL,
    yes_mid           REAL,                 -- (yes_bid + yes_ask) / 2 when both present
    no_mid            REAL,
    spread            REAL,                 -- yes_ask - yes_bid when both present
    PRIMARY KEY (snapshot_ts_ms, condition_id)
);
CREATE INDEX IF NOT EXISTS idx_book_condition
    ON book_snapshots(condition_id, snapshot_ts_ms);

CREATE TABLE IF NOT EXISTS cex_spot (
    snapshot_ts_ms    INTEGER NOT NULL,     -- millisecond UTC, collector clock
    underlying        TEXT NOT NULL,        -- 'BTC' | 'ETH' | 'SOL' | 'XRP'
    source            TEXT NOT NULL,        -- 'binance' | 'coinbase' (which one worked)
    bid               REAL,                 -- top-of-book bid on the CEX
    ask               REAL,                 -- top-of-book ask on the CEX
    mid               REAL,                 -- (bid + ask) / 2
    last              REAL,                 -- last trade price
    exchange_ts_ms    INTEGER,              -- exchange-reported timestamp, ms (Coinbase)
    sigma_ann         REAL,                 -- trailing-60m annualized realized vol (pre-reg §3.2)
    n_vol_bars        INTEGER,              -- count of 1-min bars used for sigma_ann
    PRIMARY KEY (snapshot_ts_ms, underlying)
);
CREATE INDEX IF NOT EXISTS idx_cex_underlying
    ON cex_spot(underlying, snapshot_ts_ms);

CREATE TABLE IF NOT EXISTS model_snapshots (
    snapshot_ts_ms    INTEGER NOT NULL,
    condition_id      TEXT NOT NULL,
    underlying        TEXT NOT NULL,
    cex_source        TEXT NOT NULL,        -- which CEX the spot/sigma came from
    spot              REAL,                 -- CEX spot mid used in the model
    strike            REAL,                 -- strike (parsed, or open-price proxy for relative mkts)
    tau_years         REAL,                 -- time-to-resolution in years
    sigma_ann         REAL,                 -- annualized realized vol used
    p_model_yes       REAL,                 -- Black-Scholes 0DTE binary P(YES) (pre-reg §3.1)
    p_market_yes_mid  REAL,                 -- Polymarket YES mid at this snapshot
    divergence        REAL,                 -- p_model_yes - p_market_yes_mid
    note              TEXT,                 -- e.g. 'ok', 'no_spot', 'relative_market_no_strike'
    PRIMARY KEY (snapshot_ts_ms, condition_id)
);
CREATE INDEX IF NOT EXISTS idx_model_condition
    ON model_snapshots(condition_id, snapshot_ts_ms);

CREATE TABLE IF NOT EXISTS cycles (
    cycle_ts          INTEGER NOT NULL PRIMARY KEY,   -- unix seconds
    duration_sec      REAL,
    n_markets         INTEGER,              -- in-scope markets discovered this cycle
    n_book_rows       INTEGER,              -- book snapshots written
    n_cex_ok          INTEGER,              -- underlyings with a usable spot
    n_cex_binance     INTEGER,              -- underlyings served by Binance
    n_cex_coinbase    INTEGER,              -- underlyings served by Coinbase fallback
    n_model_rows      INTEGER,              -- model snapshots written
    n_errors          INTEGER,
    notes             TEXT
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open (creating parent dirs + schema as needed) the collector DB."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_market(conn: sqlite3.Connection, *, condition_id: str,
                  market_slug: str, question: Optional[str], underlying: str,
                  family: str, horizon: str, market_kind: str,
                  strike: Optional[float], resolve_ts: int,
                  start_ts: Optional[int], yes_token: str, no_token: str,
                  resolution_source: Optional[str], now_ts: int) -> None:
    """Insert a newly-seen market, or bump last_seen_ts on a known one."""
    conn.execute(
        """
        INSERT INTO markets
            (condition_id, market_slug, question, underlying, family, horizon,
             market_kind, strike, resolve_ts, start_ts, yes_token, no_token,
             resolution_source, first_seen_ts, last_seen_ts)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(condition_id) DO UPDATE SET
            last_seen_ts      = excluded.last_seen_ts,
            question          = excluded.question,
            resolution_source = excluded.resolution_source
        """,
        (condition_id, market_slug, question, underlying, family, horizon,
         market_kind, strike, resolve_ts, start_ts, yes_token, no_token,
         resolution_source, now_ts, now_ts),
    )


def insert_book_snapshots(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """rows: (snapshot_ts_ms, condition_id, yes_bid, yes_ask, yes_bid_size,
              yes_ask_size, no_bid, no_ask, no_bid_size, no_ask_size,
              yes_mid, no_mid, spread)"""
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO book_snapshots
            (snapshot_ts_ms, condition_id, yes_bid, yes_ask, yes_bid_size,
             yes_ask_size, no_bid, no_ask, no_bid_size, no_ask_size,
             yes_mid, no_mid, spread)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows,
    )


def insert_cex_spot(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """rows: (snapshot_ts_ms, underlying, source, bid, ask, mid, last,
              exchange_ts_ms, sigma_ann, n_vol_bars)"""
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO cex_spot
            (snapshot_ts_ms, underlying, source, bid, ask, mid, last,
             exchange_ts_ms, sigma_ann, n_vol_bars)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """, rows,
    )


def insert_model_snapshots(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """rows: (snapshot_ts_ms, condition_id, underlying, cex_source, spot,
              strike, tau_years, sigma_ann, p_model_yes, p_market_yes_mid,
              divergence, note)"""
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO model_snapshots
            (snapshot_ts_ms, condition_id, underlying, cex_source, spot,
             strike, tau_years, sigma_ann, p_model_yes, p_market_yes_mid,
             divergence, note)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows,
    )


def insert_cycle(conn: sqlite3.Connection, *, cycle_ts: int,
                 duration_sec: float, n_markets: int, n_book_rows: int,
                 n_cex_ok: int, n_cex_binance: int, n_cex_coinbase: int,
                 n_model_rows: int, n_errors: int, notes: str = "") -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO cycles
            (cycle_ts, duration_sec, n_markets, n_book_rows, n_cex_ok,
             n_cex_binance, n_cex_coinbase, n_model_rows, n_errors, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (cycle_ts, duration_sec, n_markets, n_book_rows, n_cex_ok,
         n_cex_binance, n_cex_coinbase, n_model_rows, n_errors, notes),
    )
