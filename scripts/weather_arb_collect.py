#!/usr/bin/env python3
"""
Weather forecast arb — single collection cycle.

Pipeline:
  1. Discover open Polymarket weather events tagged `weather` whose city
     slug is in the frozen §2.1 universe (cities.py).
  2. For each event with end_date_utc - now ∈ [18, 30]h, fetch a
     multi-model Open-Meteo forecast at the station coordinates for the
     event's target_date.
  3. Run strategy.evaluate(); persist decision + diagnostics + bin
     snapshots + forecasts into data/weather_forecast_arb.db.

DESIGN INVARIANT: this script is OBSERVE-ONLY. No orders. No paper trading
execution. The decision row records what we WOULD do; the daily resolve
cron joins decisions to outcomes to compute realized PnL at end-of-holdout.

Cron entry (every 30 min, mirrors snapshot_markets.py cadence):
    */30 * * * * cd <repo> && python3 scripts/weather_arb_collect.py >> data/weather_arb.log 2>&1

Or wrap with a long-running runner (see weather_arb_runner.py in a later iteration).
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from pathlib import Path

# Make the strategies package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from strategies.weather_forecast_arb import db, open_meteo, polymarket, strategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("weather_arb")

DEFAULT_DB = "/app/data/weather_forecast_arb.db"
DEFAULT_HEARTBEAT = "/app/data/.heartbeat_weather_arb"


def run_cycle(db_path: str) -> dict:
    """One end-to-end cycle. Returns counters for logging/heartbeat."""
    t0 = time.time()
    now_ts = int(t0)

    session = requests.Session()
    conn = db.connect(db_path)

    try:
        events = polymarket.discover_weather_events(session=session)
    except Exception as ex:
        log.error("event discovery failed: %s", ex)
        conn.close()
        return {"n_events": 0, "n_trade": 0, "n_skip": 0, "n_api_errors": 1,
                "duration_sec": time.time() - t0}

    log.info("discovered %d events in frozen universe", len(events))

    n_trade = 0
    n_skip = 0
    n_api_errors = 0
    n_in_window = 0

    for ev in events:
        hours_to_close = (ev.end_date_utc - now_ts) / 3600.0

        # Persist event + bin snapshots regardless of entry window — the
        # bin-price tape is useful for later analysis even on events we don't
        # touch this cycle.
        db.upsert_event(
            conn,
            event_id=ev.event_id, event_slug=ev.event_slug,
            kind=ev.kind, city=ev.station.city,
            station_icao=ev.station.icao, target_date=ev.target_date,
            end_date_utc=ev.end_date_utc, now_ts=now_ts,
        )
        bin_rows = []
        for b in ev.bins:
            t_low_neg_inf = 1 if (math.isinf(b.t_low) and b.t_low < 0) else 0
            t_low_db = None if t_low_neg_inf else b.t_low
            t_high_db = None if (math.isinf(b.t_high) and b.t_high > 0) else b.t_high
            bin_rows.append((
                now_ts, ev.event_id, b.condition_id, b.market_slug,
                t_low_db if t_low_db is not None else float("-1e18"),  # placeholder for sortability
                t_high_db, t_low_neg_inf, b.units,
                b.yes_price, b.no_price,
            ))
        db.insert_bin_snapshots(conn, bin_rows)

        # Only run forecast + strategy for events in the entry window.
        if not (strategy.ENTRY_WINDOW_MIN_HOURS <= hours_to_close <= strategy.ENTRY_WINDOW_MAX_HOURS):
            db.insert_decision(
                conn, snapshot_ts=now_ts, event_id=ev.event_id,
                outcome="SKIP", reason="outside_entry_window",
                hours_to_close=hours_to_close,
                mu_hat=None, sigma_hat=None, spread_raw=None,
                sum_bin_prices=None, n_models_used=0,
                chosen_condition_id=None, chosen_yes_price=None,
                chosen_model_p=None, chosen_ev=None,
                chosen_t_low=None, chosen_t_high=None,
            )
            n_skip += 1
            continue

        n_in_window += 1
        try:
            fc = open_meteo.fetch(
                lat=ev.station.lat, lon=ev.station.lon,
                tz=ev.station.tz, units=ev.station.units,
                target_date=ev.target_date,
                session=session,
            )
        except Exception as ex:
            log.warning("Open-Meteo error for %s: %s", ev.event_slug, ex)
            n_api_errors += 1
            db.insert_decision(
                conn, snapshot_ts=now_ts, event_id=ev.event_id,
                outcome="SKIP", reason="api_error",
                hours_to_close=hours_to_close,
                mu_hat=None, sigma_hat=None, spread_raw=None,
                sum_bin_prices=None, n_models_used=0,
                chosen_condition_id=None, chosen_yes_price=None,
                chosen_model_p=None, chosen_ev=None,
                chosen_t_low=None, chosen_t_high=None,
            )
            n_skip += 1
            continue

        # Persist per-model forecast rows
        fc_rows = []
        for mf in fc.models:
            fc_rows.append((
                now_ts, ev.event_id, mf.model, mf.target_date,
                mf.temp_max, mf.temp_min,
                fc.api_grid_lat, fc.api_grid_lon,
            ))
        db.insert_forecast_rows(conn, fc_rows)

        # Apply strategy
        decision = strategy.evaluate(ev, fc, now_ts)
        if decision.outcome == "TRADE":
            n_trade += 1
            assert decision.best_bin is not None
            db.insert_decision(
                conn, snapshot_ts=now_ts, event_id=ev.event_id,
                outcome="TRADE", reason="ok",
                hours_to_close=hours_to_close,
                mu_hat=decision.mu_hat, sigma_hat=decision.sigma_hat,
                spread_raw=decision.spread_raw,
                sum_bin_prices=decision.sum_bin_prices,
                n_models_used=decision.n_models_used,
                chosen_condition_id=decision.best_bin.condition_id,
                chosen_yes_price=decision.best_bin.market_yes_price,
                chosen_model_p=decision.best_bin.model_p_yes,
                chosen_ev=decision.best_bin.expected_value,
                chosen_t_low=decision.best_bin.t_low if not math.isinf(decision.best_bin.t_low) else None,
                chosen_t_high=decision.best_bin.t_high if not math.isinf(decision.best_bin.t_high) else None,
            )
            log.info("TRADE %s [%s, %s] yes=%.3f p_model=%.3f ev=%.3f",
                     ev.event_slug, decision.best_bin.t_low,
                     decision.best_bin.t_high,
                     decision.best_bin.market_yes_price or 0,
                     decision.best_bin.model_p_yes,
                     decision.best_bin.expected_value)
        else:
            n_skip += 1
            db.insert_decision(
                conn, snapshot_ts=now_ts, event_id=ev.event_id,
                outcome="SKIP", reason=decision.reason,
                hours_to_close=hours_to_close,
                mu_hat=decision.mu_hat, sigma_hat=decision.sigma_hat,
                spread_raw=decision.spread_raw,
                sum_bin_prices=decision.sum_bin_prices,
                n_models_used=decision.n_models_used,
                chosen_condition_id=(decision.best_bin.condition_id if decision.best_bin else None),
                chosen_yes_price=(decision.best_bin.market_yes_price if decision.best_bin else None),
                chosen_model_p=(decision.best_bin.model_p_yes if decision.best_bin else None),
                chosen_ev=(decision.best_bin.expected_value if decision.best_bin else None),
                chosen_t_low=None, chosen_t_high=None,
            )

    conn.commit()

    duration = time.time() - t0
    counters = {
        "n_events": len(events),
        "n_in_window": n_in_window,
        "n_trade": n_trade,
        "n_skip": n_skip,
        "n_api_errors": n_api_errors,
        "duration_sec": duration,
    }
    db.insert_cycle(
        conn, cycle_ts=now_ts, duration_sec=duration,
        n_events=len(events), n_trade=n_trade, n_skip=n_skip,
        n_api_errors=n_api_errors,
        notes=f"in_window={n_in_window}",
    )
    conn.commit()
    conn.close()

    log.info("cycle done: %s", counters)
    return counters


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.environ.get("WEATHER_ARB_DB", DEFAULT_DB))
    p.add_argument("--heartbeat", default=os.environ.get("WEATHER_ARB_HEARTBEAT",
                                                          DEFAULT_HEARTBEAT))
    args = p.parse_args()

    try:
        counters = run_cycle(args.db)
    except Exception as ex:
        log.exception("cycle failed: %s", ex)
        return 1

    # Touch heartbeat on success (Fleet_Monitor convention)
    try:
        os.makedirs(os.path.dirname(args.heartbeat) or ".", exist_ok=True)
        Path(args.heartbeat).touch()
    except Exception as ex:
        log.warning("heartbeat touch failed: %s", ex)

    return 0


if __name__ == "__main__":
    sys.exit(main())
