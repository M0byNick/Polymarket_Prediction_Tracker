"""Paper-trading tables (Stage 4 MVP).

Three tables, co-located with the snapshot DB
(``data/polymarket_snapshots.db``) so the signal monitor, paper trader,
and resolution watcher can read the snapshot book history plus their
own state from a single WAL-mode SQLite file.

State machine
-------------
::

    signal_monitor                paper_trader              resolution_watcher
    ==============                ============              ==================
    paper_signals   --status=new--> read --status=consumed-->
                                  |
                                  write paper_orders status=submitted
                                  write paper_orders status=filled
                                  write paper_positions status=open
                                                                        |
                                                                        read open
                                                                        write status=resolved
                                                                        set realized_pnl

A market is only signaled once: the signal monitor enforces "no open or
resolved paper_position for this condition_id" before emitting a new
signal row. This prevents re-entries when the NO ask dips back below
0.35 after an early fill.

All timestamps are unix seconds (UTC). All monetary amounts are USDC
(paper). Share counts are in contract units — Polymarket pays out $1
per share on the winning side.

Tables
------
paper_signals
    The monitor's log of every threshold crossing we acted on. One row
    per signal, keyed by autoincrement id; ``condition_id`` has a unique
    partial index (see ``idx_paper_signals_cid_active``) so the UNIQUE
    constraint fires only for still-active positions.
paper_orders
    The paper trader's record of submitted/filled orders. One row per
    order. For MVP taker-at-ask every signal produces exactly one
    filled order.
paper_positions
    Open and resolved positions. Keyed by order_id for traceability.
    ``realized_pnl`` stays NULL until the resolution watcher fills it.
"""
from __future__ import annotations

from . import db as _db

# --------------- DDL --------------- #

_DDL = """
CREATE TABLE IF NOT EXISTS paper_signals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id     TEXT NOT NULL,
    token_id         TEXT NOT NULL,
    side             TEXT NOT NULL,          -- 'NO' (Stage 4 MVP only emits NO)
    trigger_price    REAL NOT NULL,          -- ask that crossed threshold
    best_bid         REAL,
    best_ask         REAL,
    bid_size         REAL,
    ask_size         REAL,                   -- shares offered at best ask
    book_mid         REAL,
    book_spread      REAL,
    slug             TEXT,
    title            TEXT,
    category_v2      TEXT,
    resolve_ts       INTEGER,                -- market resolution time (unix s)
    status           TEXT NOT NULL DEFAULT 'new',
                                             -- 'new' | 'consumed' | 'rejected'
    reject_reason    TEXT,
    created_ts       INTEGER NOT NULL,
    consumed_ts      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_paper_signals_status
    ON paper_signals(status, created_ts);

CREATE INDEX IF NOT EXISTS idx_paper_signals_cid
    ON paper_signals(condition_id);

CREATE TABLE IF NOT EXISTS paper_orders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id        INTEGER NOT NULL,
    condition_id     TEXT NOT NULL,
    token_id         TEXT NOT NULL,
    side             TEXT NOT NULL,
    limit_price      REAL NOT NULL,          -- price we submitted at (= trigger for taker)
    qty_shares       REAL NOT NULL,          -- contracts
    qty_usd          REAL NOT NULL,          -- paper dollars committed
    status           TEXT NOT NULL DEFAULT 'submitted',
                                             -- 'submitted' | 'filled' | 'cancelled'
    fill_price       REAL,                   -- avg price on fill
    fill_ts          INTEGER,
    submitted_ts     INTEGER NOT NULL,
    FOREIGN KEY (signal_id) REFERENCES paper_signals(id)
);

CREATE INDEX IF NOT EXISTS idx_paper_orders_status
    ON paper_orders(status, submitted_ts);

CREATE INDEX IF NOT EXISTS idx_paper_orders_cid
    ON paper_orders(condition_id);

CREATE TABLE IF NOT EXISTS paper_positions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id           INTEGER NOT NULL,
    condition_id       TEXT NOT NULL,
    token_id           TEXT NOT NULL,
    side               TEXT NOT NULL,
    fill_price         REAL NOT NULL,
    qty_shares         REAL NOT NULL,
    qty_usd            REAL NOT NULL,
    fill_ts            INTEGER NOT NULL,
    resolve_ts         INTEGER,              -- expected resolution ts (from market metadata)
    status             TEXT NOT NULL DEFAULT 'open',
                                             -- 'open' | 'resolved' | 'void'
    resolved_outcome   TEXT,                 -- 'YES' | 'NO' | NULL
    realized_pnl       REAL,                 -- paper dollars; NULL until resolved
    resolved_ts        INTEGER,
    FOREIGN KEY (order_id) REFERENCES paper_orders(id)
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_status
    ON paper_positions(status, fill_ts);

CREATE INDEX IF NOT EXISTS idx_paper_positions_cid
    ON paper_positions(condition_id);
"""

_db.register_ddl(_DDL)


# --------------- helpers --------------- #

def has_open_or_recent_position(conn, condition_id: str) -> bool:
    """Return True iff a paper_position for this condition_id is open
    OR a paper_signal is still 'new' awaiting consumption.

    Used by the signal monitor to avoid emitting duplicate signals on
    the same market.
    """
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM paper_positions
             WHERE condition_id = ? AND status = 'open') +
            (SELECT COUNT(*) FROM paper_signals
             WHERE condition_id = ? AND status = 'new')
        """,
        (condition_id, condition_id),
    ).fetchone()
    return (row[0] or 0) > 0
