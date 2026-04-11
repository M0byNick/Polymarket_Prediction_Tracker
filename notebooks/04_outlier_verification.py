#!/usr/bin/env python3
"""
Phase 1B — Outlier verification.

Reads data/polymarket_strict_deep.db (populated by Phase 1A) and
investigates the four outliers flagged in the Step 2 findings:

    033033033                   100% WR, 1,406 fast (suspicious)
    SecondWindCapital            30% WR, $2M PnL   (extreme underdog)
    0x006cc834Cc092684F1B566    $4.55M PnL        (top of strict list, anonymous)
    0xc2494b32d65DeA3a3A8071     52% WR, 49k fast (extreme volume, tiny edge)

For each, produces:
    - Raw trade count
    - Unique market count
    - Position-level PnL reconciliation (does it match Step 2 stat?)
    - Entry price distribution (to check underdog vs favorite bias)
    - Side distribution (BUY vs SELL)
    - Trade frequency per day
    - Daily PnL trajectory
    - A plausibility verdict
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

DB_DEFAULT = "data/polymarket_strict_deep.db"

OUTLIERS = {
    "0x78d9ca97c21ec28d7d0ec6c5a04bf73d74e36ecf": None,  # 033033033 (placeholder, looked up at runtime)
    "secondwindcapital": None,
    "0x006cc834cc092684f1b566": None,
    "0xc2494b32d65dea3a3a8071": None,
}

# We'll match by name substring since wallet addrs were truncated in display.
OUTLIER_NAME_HINTS = [
    "033033033",
    "SecondWindCapital",
    "0x006cc834Cc",
    "0xc2494b32d6",
]


def percentile(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    k = (len(vals) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    if f == c:
        return vals[f]
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def find_outliers(conn) -> list:
    rows = conn.execute(
        """SELECT wallet, name, step2_total_trades, step2_fast_settling,
                  step2_realized_pnl, step2_win_rate, full_trades_pulled
           FROM strict_wallets"""
    ).fetchall()
    out = []
    for r in rows:
        wallet, name, step2_tt, step2_fs, step2_pnl, step2_wr, full_n = r
        key = (name or "") + " " + (wallet or "")
        for hint in OUTLIER_NAME_HINTS:
            if hint.lower() in key.lower():
                out.append({
                    "wallet": wallet, "name": name,
                    "step2_total_trades": step2_tt,
                    "step2_fast_settling": step2_fs,
                    "step2_realized_pnl": step2_pnl,
                    "step2_win_rate": step2_wr,
                    "full_trades_pulled": full_n,
                })
                break
    return out


def get_trades(conn, wallet: str) -> list:
    cur = conn.execute(
        """SELECT timestamp, condition_id, outcome_index, side, price, size, usdc_size,
                  outcome, title
           FROM strict_trades WHERE wallet = ? ORDER BY timestamp""",
        (wallet,),
    )
    return [dict(zip(
        ["timestamp", "condition_id", "outcome_index", "side", "price",
         "size", "usdc_size", "outcome", "title"],
        row,
    )) for row in cur.fetchall()]


def get_market_outcome(conn, cid: str, outcome_index: int):
    row = conn.execute(
        "SELECT closed, outcome_prices, end_date FROM strict_markets WHERE condition_id = ?",
        (cid,),
    ).fetchone()
    if not row:
        return None
    closed, outcome_prices, end_date = row
    if not closed or not outcome_prices:
        return None
    try:
        prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
        return float(prices[outcome_index])
    except Exception:
        return None


def compute_positions(trades: list) -> dict:
    pos = defaultdict(lambda: {"shares": 0.0, "usdc": 0.0, "n": 0})
    for t in trades:
        key = (t["condition_id"], t["outcome_index"])
        size = t["size"]
        usdc = t["usdc_size"]
        if t["side"] == "BUY":
            pos[key]["shares"] += size
            pos[key]["usdc"] -= usdc
        else:
            pos[key]["shares"] -= size
            pos[key]["usdc"] += usdc
        pos[key]["n"] += 1
    return dict(pos)


def analyze(conn, outlier: dict):
    wallet = outlier["wallet"]
    name = outlier["name"] or wallet[:12]
    print("=" * 90)
    print(f"OUTLIER: {name}   ({wallet})")
    print("=" * 90)
    print(f"Step 2 stats: total={outlier['step2_total_trades']:,}  "
          f"fast={outlier['step2_fast_settling']:,}  "
          f"pnl=${outlier['step2_realized_pnl']:,.0f}  "
          f"wr={outlier['step2_win_rate']:.3f}")
    print(f"Full pull:    {outlier['full_trades_pulled']:,} trades")

    trades = get_trades(conn, wallet)
    if not trades:
        print("  NO RAW TRADES in DB — Phase 1A may not have written for this wallet yet")
        return

    n = len(trades)
    buys = sum(1 for t in trades if t["side"] == "BUY")
    sells = n - buys

    # Trade frequency
    ts_list = [t["timestamp"] for t in trades if t["timestamp"]]
    earliest = min(ts_list)
    latest = max(ts_list)
    days = (latest - earliest) / 86400 if latest > earliest else 1.0
    per_day = n / max(days, 1)

    # Entry price distribution
    prices = [t["price"] for t in trades if t["side"] == "BUY" and t["price"] is not None]
    p_dist = {
        "n_buys": len(prices),
        "min": min(prices) if prices else None,
        "p10": percentile(prices, 10),
        "p25": percentile(prices, 25),
        "p50": percentile(prices, 50),
        "p75": percentile(prices, 75),
        "p90": percentile(prices, 90),
        "max": max(prices) if prices else None,
        "mean": sum(prices) / len(prices) if prices else None,
    }

    # Positions + realized PnL
    positions = compute_positions(trades)
    resolved_pnl = 0.0
    unresolved_pnl_estimate = 0.0
    n_resolved_pos = 0
    n_unresolved_pos = 0
    wins = 0
    losses = 0
    pushes = 0
    resolved_trade_count = 0

    for (cid, oi), p in positions.items():
        payout = get_market_outcome(conn, cid, oi)
        if payout is None:
            n_unresolved_pos += 1
            # Best estimate: assume current shares would realize at last trade price
            # (or just assume 0.5 — but we'll report separately)
            continue
        n_resolved_pos += 1
        pnl = p["shares"] * payout + p["usdc"]
        resolved_pnl += pnl
        resolved_trade_count += p["n"]
        if pnl > 1:
            wins += 1
        elif pnl < -1:
            losses += 1
        else:
            pushes += 1

    computed_wr = wins / max(wins + losses, 1)

    print(f"\nTRADE SUMMARY")
    print(f"  total trades in DB:        {n:,}")
    print(f"  buys / sells:              {buys:,} / {sells:,}")
    print(f"  earliest:                  {datetime.fromtimestamp(earliest, timezone.utc).date().isoformat()}")
    print(f"  latest:                    {datetime.fromtimestamp(latest, timezone.utc).date().isoformat()}")
    print(f"  days active:               {days:.1f}")
    print(f"  trades/day (mean):         {per_day:.1f}")
    print(f"  unique positions:          {len(positions)}")
    print(f"    resolved:                {n_resolved_pos}")
    print(f"    unresolved:              {n_unresolved_pos}")

    print(f"\nBUY-SIDE ENTRY PRICE DISTRIBUTION")
    print(f"  n_buys={p_dist['n_buys']}  min={p_dist['min']:.3f}  p10={p_dist['p10']:.3f}  "
          f"p25={p_dist['p25']:.3f}  p50={p_dist['p50']:.3f}  p75={p_dist['p75']:.3f}  "
          f"p90={p_dist['p90']:.3f}  max={p_dist['max']:.3f}  mean={p_dist['mean']:.3f}")

    print(f"\nRECONCILIATION")
    print(f"  computed realized pnl:     ${resolved_pnl:,.0f}")
    print(f"  step2 realized pnl:        ${outlier['step2_realized_pnl']:,.0f}")
    print(f"  match:                     {abs(resolved_pnl - outlier['step2_realized_pnl']) < 100}")
    print(f"  wins / losses / pushes:    {wins} / {losses} / {pushes}")
    print(f"  computed position WR:      {computed_wr:.3f}")
    print(f"  step2 trade-level WR:      {outlier['step2_win_rate']:.3f}")

    # Verdict
    print(f"\nVERDICT")
    if p_dist["p50"] is not None and p_dist["p50"] < 0.30:
        print("  profile: UNDERDOG — median entry < 0.30, consistent with high-payoff low-WR strategy")
    elif p_dist["p50"] is not None and p_dist["p50"] > 0.70:
        print("  profile: FAVORITE — median entry > 0.70, consistent with high-WR strategy")
    else:
        print("  profile: BALANCED — median entry in [0.30, 0.70]")

    if outlier["step2_win_rate"] and outlier["step2_win_rate"] >= 0.95:
        if p_dist["p50"] is not None and p_dist["p50"] >= 0.75:
            print("  100% WR is PLAUSIBLE — deep-favorite betting, losses rare but not zero")
        else:
            print("  100% WR is SUSPICIOUS — median entry not a heavy favorite, check PnL calc")

    if abs(resolved_pnl - outlier["step2_realized_pnl"]) >= 100:
        print(f"  PnL MISMATCH: computed ${resolved_pnl:,.0f} vs step2 ${outlier['step2_realized_pnl']:,.0f} "
              f"— possibly from Step 2 trimming at 50k cap or different resolution of some markets")

    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DB_DEFAULT)
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = None

    outliers = find_outliers(conn)
    if not outliers:
        print("No matching outliers found in strict_wallets")
        print("Tried name hints:", OUTLIER_NAME_HINTS)
        return 1

    print(f"Found {len(outliers)} outliers to investigate")
    print()

    for o in outliers:
        analyze(conn, o)

    return 0


if __name__ == "__main__":
    sys.exit(main())
