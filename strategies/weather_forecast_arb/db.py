"""
SQLite schema for the weather forecast arb collector.

Tables:
    events            — every (event_id, snapshot_ts) tuple we've observed
    bin_snapshots     — per-bin YES price at each snapshot
    forecasts         — per-model temp_max/temp_min for the target date at each snapshot
    decisions         — strategy decision per snapshot per event
    resolutions       — final realized outcome (populated by daily resolve cron)
    cycles            — per-cron-cycle summary (success/error counts)

WAL mode + IF NOT EXISTS migrations. Designed for append-only insert,
read-mostly EOD analysis from notebooks.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id          TEXT NOT NULL,
    event_slug        TEXT NOT NULL,
    kind              TEXT NOT NULL,        -- 'highest' | 'lowest'
    city              TEXT NOT NULL,        -- slug fragment, e.g. 'london'
    station_icao      TEXT NOT NULL,
    target_date       TEXT NOT NULL,        -- YYYY-MM-DD, station-local
    end_date_utc      INTEGER NOT NULL,
    first_seen_ts     INTEGER NOT NULL,
    last_seen_ts      INTEGER NOT NULL,
    PRIMARY KEY (event_id)
);

CREATE TABLE IF NOT EXISTS bin_snapshots (
    snapshot_ts       INTEGER NOT NULL,
    event_id          TEXT NOT NULL,
    condition_id      TEXT NOT NULL,
    market_slug       TEXT NOT NULL,
    t_low             REAL NOT NULL,
    t_high            REAL,                  -- NULL for +inf upper bin
    t_low_is_neg_inf  INTEGER NOT NULL DEFAULT 0,
    units             TEXT NOT NULL,
    yes_price         REAL,
    no_price          REAL,
    PRIMARY KEY (snapshot_ts, condition_id)
);
CREATE INDEX IF NOT EXISTS idx_bin_snapshots_event
    ON bin_snapshots(event_id, snapshot_ts);

CREATE TABLE IF NOT EXISTS forecasts (
    snapshot_ts       INTEGER NOT NULL,
    event_id          TEXT NOT NULL,
    model             TEXT NOT NULL,         -- icon_seamless | gfs_seamless | ecmwf_ifs025
    target_date       TEXT NOT NULL,
    temp_max          REAL,                  -- in event units (°C or °F)
    temp_min          REAL,
    api_grid_lat      REAL,                  -- actual cell center used by Open-Meteo
    api_grid_lon      REAL,
    PRIMARY KEY (snapshot_ts, event_id, model)
);
CREATE INDEX IF NOT EXISTS idx_forecasts_event
    ON forecasts(event_id, snapshot_ts);

CREATE TABLE IF NOT EXISTS decisions (
    snapshot_ts       INTEGER NOT NULL,
    event_id          TEXT NOT NULL,
    outcome           TEXT NOT NULL,         -- 'TRADE' | 'SKIP'
    reason            TEXT NOT NULL,
    hours_to_close    REAL,
    mu_hat            REAL,                  -- ensemble mean
    sigma_hat         REAL,                  -- clamped dispersion
    spread_raw        REAL,
    sum_bin_prices    REAL,
    n_models_used     INTEGER,
    chosen_condition_id  TEXT,                -- NULL on SKIP
    chosen_yes_price  REAL,                  -- NULL on SKIP
    chosen_model_p    REAL,                  -- NULL on SKIP
    chosen_ev         REAL,                  -- NULL on SKIP
    chosen_t_low      REAL,
    chosen_t_high     REAL,
    PRIMARY KEY (snapshot_ts, event_id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_event
    ON decisions(event_id, snapshot_ts);

CREATE TABLE IF NOT EXISTS resolutions (
    event_id          TEXT NOT NULL,
    condition_id      TEXT NOT NULL,
    resolved_ts       INTEGER NOT NULL,
    yes_winner        INTEGER NOT NULL,      -- 1 if YES won, 0 if NO
    realized_temp     REAL,                  -- in event units, if known
    resolution_source TEXT,
    PRIMARY KEY (condition_id)
);

CREATE TABLE IF NOT EXISTS cycles (
    cycle_ts          INTEGER NOT NULL PRIMARY KEY,
    duration_sec      REAL,
    n_events          INTEGER,
    n_trade           INTEGER,
    n_skip            INTEGER,
    n_api_errors      INTEGER,
    notes             TEXT
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_event(conn: sqlite3.Connection, *, event_id: str, event_slug: str,
                  kind: str, city: str, station_icao: str, target_date: str,
                  end_date_utc: int, now_ts: int) -> None:
    conn.execute(
        """
        INSERT INTO events
            (event_id, event_slug, kind, city, station_icao, target_date,
             end_date_utc, first_seen_ts, last_seen_ts)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_id) DO UPDATE SET
            last_seen_ts = excluded.last_seen_ts
        """,
        (event_id, event_slug, kind, city, station_icao, target_date,
         end_date_utc, now_ts, now_ts),
    )


def insert_bin_snapshots(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """rows: (snapshot_ts, event_id, condition_id, market_slug, t_low, t_high,
              t_low_is_neg_inf, units, yes_price, no_price)"""
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO bin_snapshots
            (snapshot_ts, event_id, condition_id, market_slug, t_low, t_high,
             t_low_is_neg_inf, units, yes_price, no_price)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """, rows,
    )


def insert_forecast_rows(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """rows: (snapshot_ts, event_id, model, target_date, temp_max, temp_min,
              api_grid_lat, api_grid_lon)"""
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO forecasts
            (snapshot_ts, event_id, model, target_date, temp_max, temp_min,
             api_grid_lat, api_grid_lon)
        VALUES (?,?,?,?,?,?,?,?)
        """, rows,
    )


def insert_decision(conn: sqlite3.Connection, *, snapshot_ts: int,
                     event_id: str, outcome: str, reason: str,
                     hours_to_close: Optional[float],
                     mu_hat: Optional[float], sigma_hat: Optional[float],
                     spread_raw: Optional[float],
                     sum_bin_prices: Optional[float],
                     n_models_used: int,
                     chosen_condition_id: Optional[str],
                     chosen_yes_price: Optional[float],
                     chosen_model_p: Optional[float],
                     chosen_ev: Optional[float],
                     chosen_t_low: Optional[float],
                     chosen_t_high: Optional[float]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO decisions
            (snapshot_ts, event_id, outcome, reason, hours_to_close,
             mu_hat, sigma_hat, spread_raw, sum_bin_prices, n_models_used,
             chosen_condition_id, chosen_yes_price, chosen_model_p,
             chosen_ev, chosen_t_low, chosen_t_high)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (snapshot_ts, event_id, outcome, reason, hours_to_close,
         mu_hat, sigma_hat, spread_raw, sum_bin_prices, n_models_used,
         chosen_condition_id, chosen_yes_price, chosen_model_p,
         chosen_ev, chosen_t_low, chosen_t_high),
    )


def insert_cycle(conn: sqlite3.Connection, *, cycle_ts: int,
                  duration_sec: float, n_events: int, n_trade: int,
                  n_skip: int, n_api_errors: int, notes: str = "") -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO cycles
            (cycle_ts, duration_sec, n_events, n_trade, n_skip, n_api_errors, notes)
        VALUES (?,?,?,?,?,?,?)
        """,
        (cycle_ts, duration_sec, n_events, n_trade, n_skip, n_api_errors, notes),
    )
