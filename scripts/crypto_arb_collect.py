#!/usr/bin/env python3
"""
Crypto-binary latency arb — single bounded collection cycle.

Pipeline (one cycle):
  1. Discover open Polymarket crypto-binary markets for BTC/ETH/SOL/XRP in
     the pre-reg §2 universe (hourly + 15-minute up/down + fixed-strike).
  2. Fetch CLOB top-of-book (best bid/ask + sizes) for the YES and NO token
     of every in-scope market via the batch /books endpoint.
  3. Fetch a centralized-exchange spot reference per underlying — Binance
     primary, Coinbase fallback (pre-reg §2.3) — plus a trailing-60-minute
     annualized realized-vol estimate.
  4. Compute the pre-reg §3 Black-Scholes 0DTE binary model probability for
     each strike market (convenience column).
  5. Persist markets + book snapshots + CEX spot + model snapshots into the
     SQLite DB. Write a per-cycle health row.

DESIGN INVARIANT: OBSERVE-ONLY. This script captures raw market data. It
places NO orders, touches NO wallet, computes NO position. The §3 model
column is a convenience for later holdout scoring, not a trade signal.

Run standalone:
    python3 scripts/crypto_arb_collect.py --db /path/to/crypto_arb.db

Or under the long-running loop (scripts/crypto_arb_runner.py), which is what
the launchd daemon uses.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# Make the strategies package importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from strategies.crypto_latency_arb import cex_spot, db, model, polymarket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("crypto_arb")

DEFAULT_DB = "/Users/nicholasmorihisa/trading-data/crypto/crypto_arb.db"
DEFAULT_HEARTBEAT = "/Users/nicholasmorihisa/trading-data/crypto/.heartbeat_crypto_arb"


def _now_ms() -> int:
    """Current UTC time in integer milliseconds (pre-reg §3.5 clock)."""
    return time.time_ns() // 1_000_000


def run_cycle(db_path: str,
              capture_horizon_s: int = polymarket.DEFAULT_CAPTURE_HORIZON_S
              ) -> dict:
    """
    Execute one end-to-end collection cycle. Returns counters for logging.

    `capture_horizon_s` bounds dense snapshotting to markets resolving within
    that many seconds (collector-efficiency parameter, not a pre-reg
    hyperparameter — see polymarket.DEFAULT_CAPTURE_HORIZON_S).
    """
    t0 = time.time()
    cycle_ts = int(t0)

    session = requests.Session()
    conn = db.connect(db_path)
    n_errors = 0

    # --- 1. discover in-scope markets --------------------------------------
    try:
        markets = polymarket.discover_markets(
            session=session, capture_horizon_s=capture_horizon_s)
    except Exception as ex:
        log.error("market discovery failed: %s", ex)
        db.insert_cycle(conn, cycle_ts=cycle_ts, duration_sec=time.time() - t0,
                        n_markets=0, n_book_rows=0, n_cex_ok=0,
                        n_cex_binance=0, n_cex_coinbase=0, n_model_rows=0,
                        n_errors=1, notes="discovery_failed")
        conn.commit()
        conn.close()
        return {"n_markets": 0, "n_book_rows": 0, "n_errors": 1,
                "duration_sec": time.time() - t0}

    by_underlying: dict[str, int] = {}
    for m in markets:
        by_underlying[m.underlying] = by_underlying.get(m.underlying, 0) + 1
    log.info("discovered %d in-scope markets %s", len(markets),
             {k: by_underlying.get(k, 0) for k in polymarket.IN_SCOPE_UNDERLYINGS})

    # --- 2. fetch CLOB top-of-book for every market's YES + NO token -------
    all_tokens: list[str] = []
    for m in markets:
        all_tokens.append(m.yes_token)
        all_tokens.append(m.no_token)
    try:
        books = polymarket.fetch_books(all_tokens, session=session)
    except Exception as ex:
        log.error("CLOB book fetch failed: %s", ex)
        books = {}
        n_errors += 1

    # --- 3. fetch CEX spot per underlying (Binance primary / Coinbase fb) --
    try:
        spots = cex_spot.get_all_spots(session=session)
    except Exception as ex:
        log.error("CEX spot fetch failed: %s", ex)
        spots = {}
        n_errors += 1

    n_cex_binance = sum(1 for q in spots.values() if q.source == "binance")
    n_cex_coinbase = sum(1 for q in spots.values() if q.source == "coinbase")
    for sym, q in spots.items():
        log.info("CEX %s via %s: mid=%.6f sigma_ann=%s (n_bars=%d)",
                 sym, q.source, q.mid or 0.0,
                 f"{q.sigma_ann:.4f}" if q.sigma_ann is not None else "n/a",
                 q.n_vol_bars)

    # One snapshot timestamp for the whole cycle keeps the book rows, CEX
    # rows and model rows joinable on a shared instant (pre-reg §3.5).
    snapshot_ms = _now_ms()

    # --- 4 + 5. persist CEX spot, books, model -----------------------------
    cex_rows = []
    for sym, q in spots.items():
        cex_rows.append((
            snapshot_ms, sym, q.source, q.bid, q.ask, q.mid, q.last,
            q.exchange_ts_ms, q.sigma_ann, q.n_vol_bars,
        ))
    db.insert_cex_spot(conn, cex_rows)

    book_rows = []
    model_rows = []
    for m in markets:
        db.upsert_market(
            conn, condition_id=m.condition_id, market_slug=m.market_slug,
            question=m.question, underlying=m.underlying, family=m.family,
            horizon=m.horizon, market_kind=m.market_kind, strike=m.strike,
            resolve_ts=m.resolve_ts, start_ts=m.start_ts,
            yes_token=m.yes_token, no_token=m.no_token,
            resolution_source=m.resolution_source, now_ts=cycle_ts,
        )

        yes_b = books.get(m.yes_token)
        no_b = books.get(m.no_token)
        yes_mid = yes_b.mid if yes_b is not None else None
        no_mid = no_b.mid if no_b is not None else None
        spread = None
        if yes_b is not None and yes_b.best_bid is not None and yes_b.best_ask is not None:
            spread = yes_b.best_ask - yes_b.best_bid
        book_rows.append((
            snapshot_ms, m.condition_id,
            yes_b.best_bid if yes_b else None,
            yes_b.best_ask if yes_b else None,
            yes_b.bid_size if yes_b else None,
            yes_b.ask_size if yes_b else None,
            no_b.best_bid if no_b else None,
            no_b.best_ask if no_b else None,
            no_b.bid_size if no_b else None,
            no_b.ask_size if no_b else None,
            yes_mid, no_mid, spread,
        ))

        # §3 convenience model column.
        q = spots.get(m.underlying)
        spot_mid = q.mid if q is not None else None
        sigma = q.sigma_ann if q is not None else None
        cex_src = q.source if q is not None else "none"
        mr = model.evaluate(
            market_kind=m.market_kind, spot=spot_mid, strike=m.strike,
            sigma_ann=sigma, snapshot_ts=snapshot_ms / 1000.0,
            resolve_ts=m.resolve_ts,
        )
        divergence = None
        if mr.p_model_yes is not None and yes_mid is not None:
            divergence = mr.p_model_yes - yes_mid
        model_rows.append((
            snapshot_ms, m.condition_id, m.underlying, cex_src, spot_mid,
            m.strike, mr.tau_years, sigma, mr.p_model_yes, yes_mid,
            divergence, mr.note,
        ))

    db.insert_book_snapshots(conn, book_rows)
    db.insert_model_snapshots(conn, model_rows)
    conn.commit()

    duration = time.time() - t0
    counters = {
        "n_markets": len(markets),
        "n_book_rows": len(book_rows),
        "n_cex_ok": len(spots),
        "n_cex_binance": n_cex_binance,
        "n_cex_coinbase": n_cex_coinbase,
        "n_model_rows": len(model_rows),
        "n_errors": n_errors,
        "duration_sec": duration,
    }
    db.insert_cycle(
        conn, cycle_ts=cycle_ts, duration_sec=duration,
        n_markets=len(markets), n_book_rows=len(book_rows),
        n_cex_ok=len(spots), n_cex_binance=n_cex_binance,
        n_cex_coinbase=n_cex_coinbase, n_model_rows=len(model_rows),
        n_errors=n_errors,
        notes=f"underlyings={by_underlying}",
    )
    conn.commit()
    conn.close()

    log.info("cycle done: %s", counters)
    return counters


def main() -> int:
    p = argparse.ArgumentParser(description="Crypto latency arb collector — one cycle")
    p.add_argument("--db", default=os.environ.get("CRYPTO_ARB_DB", DEFAULT_DB))
    p.add_argument("--heartbeat",
                   default=os.environ.get("CRYPTO_ARB_HEARTBEAT", DEFAULT_HEARTBEAT))
    p.add_argument(
        "--capture-horizon-min", type=int,
        default=int(os.environ.get(
            "CRYPTO_ARB_CAPTURE_HORIZON_MIN",
            polymarket.DEFAULT_CAPTURE_HORIZON_S // 60)),
        help="dense-snapshot only markets resolving within this many minutes "
             "(collector-efficiency bound; default 90)")
    args = p.parse_args()

    try:
        run_cycle(args.db, capture_horizon_s=args.capture_horizon_min * 60)
    except Exception as ex:
        log.exception("cycle failed: %s", ex)
        return 1

    # Touch heartbeat on success (Fleet_Monitor heartbeat-file convention).
    try:
        os.makedirs(os.path.dirname(args.heartbeat) or ".", exist_ok=True)
        Path(args.heartbeat).touch()
    except Exception as ex:
        log.warning("heartbeat touch failed: %s", ex)

    return 0


if __name__ == "__main__":
    sys.exit(main())
