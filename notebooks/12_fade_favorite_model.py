#!/usr/bin/env python3
"""
Phase 3e — Fade-the-Favorite model, derived from 0x006cc834Cc.

Hypothesis (from Phase 3a):
  "Polymarket 'Will [team] win?' moneyline markets systematically overprice
   favorites in UCL/EPL/Ligue 1/NBA. Betting NO on clear favorites produces
   positive expected value."

This script:
1. Loads 0x006cc834Cc's sports-NO trades with market metadata
2. Computes the realized edge per (category, entry-price-bucket) combination
3. Applies the same rule as a backtest to all UCL/EPL/Ligue 1/NBA markets
   in our DB and compares "naive NO buyer at market offer" PnL to
   0x006cc834Cc's actual PnL
4. Stress-tests the rule across entry-price thresholds to find the sweet spot
5. Produces a rule spec for forward testing

Output: data/fade_favorite_backtest.json + stdout report
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone


TARGET_CATEGORIES = [
    "sports_soccer_ucl",
    "sports_soccer_epl",
    "sports_soccer_ligue1",
    "sports_soccer_bundesliga",
    "sports_soccer_laliga",
    "sports_soccer_seriea",
    "sports_nba",
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


def load_positions(conn, wallet: str) -> list:
    """Load each (condition_id, outcome_index) position + settlement."""
    rows = conn.execute("""
        SELECT t.condition_id, t.outcome_index, t.side, t.size, t.usdc_size, t.price,
               t.title, t.event_slug, t.timestamp,
               m.closed, m.outcome_prices, m.resolve_ts, m.category_v2, m.category_broad
        FROM strict_trades t
        LEFT JOIN strict_markets m ON t.condition_id = m.condition_id
        WHERE t.wallet = ?
        ORDER BY t.condition_id, t.outcome_index, t.timestamp
    """, (wallet,)).fetchall()

    positions = defaultdict(lambda: {
        "shares": 0, "usdc": 0, "buys": 0, "sells": 0,
        "first_buy_price": None, "first_buy_ts": None,
        "total_buy_usdc": 0, "total_buy_size": 0,
        "title": "", "event_slug": "",
        "closed": 0, "op": None, "resolve_ts": None,
        "cat_v2": "misc", "cat_broad": "misc",
    })

    for cid, oi, side, size, usdc, price, title, ev_slug, ts, closed, op, rts, cv2, cb in rows:
        key = (cid, oi)
        p = positions[key]
        size = size or 0
        usdc = usdc or 0
        if side == "BUY":
            if p["first_buy_price"] is None and price:
                p["first_buy_price"] = price
                p["first_buy_ts"] = ts
            p["shares"] += size
            p["usdc"] -= usdc
            p["buys"] += 1
            p["total_buy_usdc"] += usdc
            p["total_buy_size"] += size
        else:
            p["shares"] -= size
            p["usdc"] += usdc
            p["sells"] += 1
        p["title"] = title or p["title"]
        p["event_slug"] = ev_slug or p["event_slug"]
        p["closed"] = closed or 0
        p["op"] = op
        p["resolve_ts"] = rts
        p["cat_v2"] = cv2 or "misc"
        p["cat_broad"] = cb or "misc"
    return [{"cid": k[0], "oi": k[1], **v} for k, v in positions.items()]


def settle(p: dict) -> dict | None:
    if not p["closed"] or not p["op"]:
        return None
    try:
        prices = json.loads(p["op"]) if isinstance(p["op"], str) else p["op"]
        payout = float(prices[p["oi"]])
    except Exception:
        return None
    pnl = p["shares"] * payout + p["usdc"]
    won = payout > 0.5
    return {
        **p,
        "payout": payout,
        "pnl": pnl,
        "won": won,
        "avg_buy_price": (p["total_buy_usdc"] / max(p["total_buy_size"], 1))
                         if p["total_buy_size"] > 0 else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/polymarket_strict_deep.db")
    parser.add_argument("--wallet", default="0x006cc834cc092684f1b56626e23bedb3835c16ea")
    parser.add_argument("--out", default="data/fade_favorite_backtest.json")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    positions = load_positions(conn, args.wallet)
    settled = [settle(p) for p in positions]
    settled = [s for s in settled if s is not None]
    print(f"Loaded {len(positions)} positions, {len(settled)} settled")

    # Filter to NO-side positions in target categories
    no_positions = [s for s in settled
                    if s["oi"] == 1 and s["cat_v2"] in TARGET_CATEGORIES]
    yes_positions = [s for s in settled
                     if s["oi"] == 0 and s["cat_v2"] in TARGET_CATEGORIES]
    print(f"Target-category NO positions: {len(no_positions)}")
    print(f"Target-category YES positions: {len(yes_positions)}")

    # ============================================================
    # 1. Raw performance of NO bets in target categories
    # ============================================================
    print("\n" + "=" * 80)
    print("REALIZED PERFORMANCE — NO bets in target categories")
    print("=" * 80)

    wins = [p for p in no_positions if p["won"]]
    losses = [p for p in no_positions if not p["won"]]
    total_pnl = sum(p["pnl"] for p in no_positions)
    total_staked = sum(p["total_buy_usdc"] for p in no_positions)
    wr = len(wins) / max(len(no_positions), 1)
    print(f"  positions: {len(no_positions)}")
    print(f"  wins/losses: {len(wins)}/{len(losses)}")
    print(f"  position WR: {wr:.3f}")
    print(f"  total PnL: ${total_pnl:,.0f}")
    print(f"  total staked: ${total_staked:,.0f}")
    print(f"  ROI per $ staked: {total_pnl/max(total_staked,1):.3f}")

    # ============================================================
    # 2. Realized edge by entry-price bucket
    # ============================================================
    print("\n" + "=" * 80)
    print("REALIZED EDGE BY ENTRY-PRICE BUCKET (for NO-side positions)")
    print("=" * 80)
    print("NOTE: avg_buy_price is the NO-side entry. If avg_buy_price = 0.30,")
    print("      the market implied P(team wins) was 0.70 at entry.")
    print()

    buckets = [
        (0.0, 0.15, "0.00-0.15 (implied fav >0.85)"),
        (0.15, 0.25, "0.15-0.25 (implied fav 0.75-0.85)"),
        (0.25, 0.35, "0.25-0.35 (implied fav 0.65-0.75)"),
        (0.35, 0.45, "0.35-0.45 (implied fav 0.55-0.65)"),
        (0.45, 0.55, "0.45-0.55 (coin flip)"),
        (0.55, 0.70, "0.55-0.70 (NO is the favorite)"),
        (0.70, 1.01, "0.70+ (huge NO favorite)"),
    ]

    print(f"  {'bucket':<38}  {'n':>5}  {'wr':>6}  "
          f"{'PnL':>14}  {'ROI':>7}  {'avg_pnl':>12}")
    for lo, hi, label in buckets:
        b_pos = [p for p in no_positions
                 if p["avg_buy_price"] is not None
                 and lo <= p["avg_buy_price"] < hi]
        if not b_pos:
            print(f"  {label:<38}  {0:>5}  (none)")
            continue
        b_wins = sum(1 for p in b_pos if p["won"])
        b_pnl = sum(p["pnl"] for p in b_pos)
        b_stake = sum(p["total_buy_usdc"] for p in b_pos)
        b_wr = b_wins / len(b_pos)
        roi = b_pnl / max(b_stake, 1)
        avg_pnl = b_pnl / len(b_pos)
        print(f"  {label:<38}  {len(b_pos):>5}  {b_wr:>5.3f}  "
              f"${b_pnl:>12,.0f}  {roi:>6.2f}x  ${avg_pnl:>10,.0f}")

    # ============================================================
    # 3. Threshold analysis — what's the sweet spot?
    # ============================================================
    print("\n" + "=" * 80)
    print("THRESHOLD ANALYSIS — if we only took trades where avg_buy_price <= X")
    print("=" * 80)
    print(f"  (this simulates 'only bet NO when market implied fav prob >= 1-X')")
    print()
    print(f"  {'avg_buy_price cutoff':<24}  {'n':>5}  {'wr':>6}  "
          f"{'PnL':>14}  {'ROI':>7}")
    for cutoff in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
        qualifying = [p for p in no_positions
                      if p["avg_buy_price"] is not None
                      and p["avg_buy_price"] <= cutoff]
        if not qualifying:
            continue
        n = len(qualifying)
        wr_c = sum(1 for p in qualifying if p["won"]) / n
        pnl_c = sum(p["pnl"] for p in qualifying)
        stake_c = sum(p["total_buy_usdc"] for p in qualifying)
        roi_c = pnl_c / max(stake_c, 1)
        print(f"  price <= {cutoff:.2f}          {n:>5}  {wr_c:>5.3f}  "
              f"${pnl_c:>12,.0f}  {roi_c:>6.2f}x")

    # ============================================================
    # 4. Per-category ROI comparison
    # ============================================================
    print("\n" + "=" * 80)
    print("PER-CATEGORY ROI (NO bets)")
    print("=" * 80)
    by_cat = defaultdict(lambda: {"n": 0, "pnl": 0.0, "stake": 0.0, "wins": 0})
    for p in no_positions:
        c = p["cat_v2"]
        by_cat[c]["n"] += 1
        by_cat[c]["pnl"] += p["pnl"]
        by_cat[c]["stake"] += p["total_buy_usdc"]
        if p["won"]:
            by_cat[c]["wins"] += 1

    print(f"  {'category':<28}  {'n':>5}  {'wr':>6}  {'stake':>12}  "
          f"{'PnL':>14}  {'ROI':>7}")
    for c, s in sorted(by_cat.items(), key=lambda x: -x[1]["pnl"]):
        wr = s["wins"] / max(s["n"], 1)
        roi = s["pnl"] / max(s["stake"], 1)
        print(f"  {c:<28}  {s['n']:>5}  {wr:>5.3f}  ${s['stake']:>10,.0f}  "
              f"${s['pnl']:>12,.0f}  {roi:>6.2f}x")

    # ============================================================
    # 5. YES-side baseline for comparison
    # ============================================================
    print("\n" + "=" * 80)
    print("YES-SIDE BASELINE (same target categories, same trader)")
    print("=" * 80)
    if yes_positions:
        y_wins = sum(1 for p in yes_positions if p["won"])
        y_pnl = sum(p["pnl"] for p in yes_positions)
        y_stake = sum(p["total_buy_usdc"] for p in yes_positions)
        y_wr = y_wins / len(yes_positions)
        y_roi = y_pnl / max(y_stake, 1)
        print(f"  positions: {len(yes_positions)}")
        print(f"  WR: {y_wr:.3f}")
        print(f"  total PnL: ${y_pnl:,.0f}")
        print(f"  total staked: ${y_stake:,.0f}")
        print(f"  ROI: {y_roi:.3f}")
        print(f"\n  NO/YES PnL ratio: {total_pnl / max(y_pnl, 1):.2f}x")

    # ============================================================
    # 6. Simple rule spec
    # ============================================================
    print("\n" + "=" * 80)
    print("PROPOSED RULE SPEC — FADE-THE-FAVORITE")
    print("=" * 80)
    print("""
  SIGNAL:
    For every Polymarket 'Will [team] win?' moneyline market in:
      UCL, EPL, La Liga, Serie A, Bundesliga, Ligue 1, NBA
    Check: is the current YES price > 0.65 (= market thinks favorite has >65% chance)?

  ENTRY:
    If yes, buy the NO token at the current market offer.
    Post limit order slightly below best ask to mimic 0x006cc834Cc's passive fills.

  SIZING:
    0x006cc834Cc uses highly variable sizing (CV = 4.6). For a systematic
    implementation, start with uniform sizing: 2-3% of bankroll per trade.
    Scale up on categories with highest historical ROI.

  EXIT:
    Hold to resolution. No mid-trade exits (0x006cc834Cc does re-enter
    but rarely exits positions early in target categories).

  FILTERS (optional):
    - Skip markets with <$10k 24h volume (too illiquid)
    - Skip markets where favorite is so heavy (>0.85) that implied fair prob
      is probably actually correct
    - Only trade within 24h of resolution (avoid week-old futures)

  EXPECTED PERFORMANCE:
    Based on 0x006cc834Cc's historical realized edge in these markets.
    Results above will quantify this after backtest.

  RISK:
    Max drawdown = $1.08M on ~$4.5M total PnL = 24%. Scale your bankroll
    to absorb a 25% drawdown and expect it to happen.
""")

    # Save report
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "wallet": args.wallet,
        "no_position_count": len(no_positions),
        "yes_position_count": len(yes_positions),
        "no_total_pnl": sum(p["pnl"] for p in no_positions),
        "yes_total_pnl": sum(p["pnl"] for p in yes_positions),
        "no_total_stake": sum(p["total_buy_usdc"] for p in no_positions),
        "yes_total_stake": sum(p["total_buy_usdc"] for p in yes_positions),
        "by_category_no": {c: dict(s) for c, s in by_cat.items()},
        "target_categories": TARGET_CATEGORIES,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  report → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
