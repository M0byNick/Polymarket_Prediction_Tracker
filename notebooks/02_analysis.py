#!/usr/bin/env python3
"""
Step 2 analysis — reads polymarket_step2_summary.json and produces:

1. Filter funnel
2. Distribution percentiles (trade count, fast-settling, PnL, WR, days trading)
3. Category share (all trades vs fast-settling trades)
4. Source comparison (which leaderboard produced strict survivors?)
5. Top strict + honorable survivors

Text output only. Plotting comes later in a notebook if useful.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict


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


def dist_line(label: str, vals, fmt: str = "{:.1f}"):
    vals = [v for v in vals if v is not None]
    if not vals:
        print(f"  {label:<24}  (no data)")
        return
    def f(x): return fmt.format(x) if x is not None else "-"
    print(f"  {label:<24}  n={len(vals):>4}  "
          f"min={f(min(vals)):>12}  "
          f"p25={f(percentile(vals,25)):>12}  "
          f"p50={f(percentile(vals,50)):>12}  "
          f"p75={f(percentile(vals,75)):>12}  "
          f"p90={f(percentile(vals,90)):>12}  "
          f"p99={f(percentile(vals,99)):>12}  "
          f"max={f(max(vals)):>12}  "
          f"mean={f(sum(vals)/len(vals)):>12}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", default="data/polymarket_step2_summary.json")
    args = parser.parse_args()

    with open(args.summary) as f:
        data = json.load(f)

    traders = data["traders"]
    funnel = data["funnel"]
    params = data.get("params", {})
    api = data.get("api_stats", {})

    print("=" * 100)
    print("POLYMARKET STEP 2 ANALYSIS")
    print("=" * 100)
    print(f"Started:      {data.get('started_utc','?')}")
    print(f"Elapsed:      {data.get('elapsed_seconds','?')}s")
    print(f"Params:       {params}")
    print(f"API stats:    {api}")
    print()

    # ------------------------- funnel -------------------------
    print("FILTER FUNNEL")
    print("-" * 100)
    total = funnel.get("universe_total", 1) or 1
    for k, v in funnel.items():
        pct = 100.0 * v / total if k != "universe_total" else 100.0
        print(f"  {k:<36}  {v:>6}   ({pct:>5.1f}% of universe)")
    print()

    # ------------------------- distributions -------------------------
    print("DISTRIBUTIONS")
    print("-" * 100)
    dist_line("total_trades", [t["total_trades"] for t in traders], "{:,.0f}")
    dist_line("fast_settling_trades", [t["fast_settling_trade_count"] for t in traders], "{:,.0f}")
    dist_line("resolved_trades", [t["resolved_trade_count"] for t in traders], "{:,.0f}")
    dist_line("unique_markets", [t["unique_markets"] for t in traders], "{:,.0f}")
    dist_line("days_trading", [t["days_trading"] for t in traders], "{:.1f}")
    dist_line("total_realized_pnl", [t["total_realized_pnl"] for t in traders], "${:,.0f}")
    dist_line("pnl_per_trade", [t["pnl_per_trade"] for t in traders], "${:,.2f}")
    dist_line("win_rate", [t["win_rate"] for t in traders], "{:.3f}")
    print()

    # ------------------------- category share -------------------------
    print("CATEGORY TRADE SHARE")
    print("-" * 100)
    cat_all: dict = defaultdict(int)
    cat_fast: dict = defaultdict(int)
    for t in traders:
        for c, n in (t.get("category_trade_counts") or {}).items():
            cat_all[c] += n
        for c, n in (t.get("category_fast_settling_counts") or {}).items():
            cat_fast[c] += n
    total_all = sum(cat_all.values()) or 1
    total_fast = sum(cat_fast.values()) or 1
    print(f"  {'category':<12}  {'all_trades':>12}  {'% all':>8}  "
          f"{'fast_settling':>14}  {'% fast':>8}")
    for cat in sorted(cat_all.keys(), key=lambda c: -cat_all[c]):
        print(f"  {cat:<12}  {cat_all[cat]:>12,}  "
              f"{100.0 * cat_all[cat] / total_all:>7.1f}%  "
              f"{cat_fast.get(cat, 0):>14,}  "
              f"{100.0 * cat_fast.get(cat, 0) / total_fast:>7.1f}%")
    print()

    # ------------------------- source comparison -------------------------
    print("BY LEADERBOARD SOURCE (traders may appear in multiple)")
    print("-" * 100)
    by_src: dict = defaultdict(list)
    for t in traders:
        for s in t.get("sources", []):
            by_src[s].append(t)
    for src in sorted(by_src.keys()):
        ts = by_src[src]
        n = len(ts)
        strict = sum(1 for t in ts if t["passes_strict"])
        honor = sum(1 for t in ts if t["passes_honorable"])
        avg_trades = statistics.mean([t["total_trades"] for t in ts]) if ts else 0
        med_fast = statistics.median([t["fast_settling_trade_count"] for t in ts]) if ts else 0
        print(f"  {src:<18}  n={n:>4}  strict={strict:>3}  honorable={honor:>3}  "
              f"avg_trades={avg_trades:>7,.0f}  med_fast_settling={med_fast:>6,.0f}")
    print()

    # ------------------------- strict survivors -------------------------
    strict = sorted([t for t in traders if t["passes_strict"]],
                    key=lambda t: -(t["total_realized_pnl"] or 0))
    print(f"STRICT-TIER SURVIVORS: {len(strict)}")
    print("-" * 100)
    if strict:
        for t in strict[:25]:
            wr = t.get("win_rate")
            pv = t.get("bootstrap_pvalue")
            print(f"  {t['name'][:24]:<24}  "
                  f"pnl=${t['total_realized_pnl'] or 0:>11,.0f}  "
                  f"wr={wr:.3f}  "
                  f"fast={t['fast_settling_trade_count']:>6,}  "
                  f"p={pv:.3f}  "
                  f"cats={t['counted_cats_strict']}")
    else:
        print("  (none)")
    print()

    # ------------------------- honorable-only -------------------------
    honor = sorted([t for t in traders if t["passes_honorable"] and not t["passes_strict"]],
                   key=lambda t: -(t["total_realized_pnl"] or 0))
    print(f"HONORABLE-ONLY TIER: {len(honor)}")
    print("-" * 100)
    if honor:
        for t in honor[:15]:
            pnl = t["total_realized_pnl"] or 0
            wr = t.get("win_rate")
            wr_s = f"{wr:.3f}" if wr is not None else "  -  "
            print(f"  {t['name'][:24]:<24}  "
                  f"pnl=${pnl:>11,.0f}  "
                  f"wr={wr_s}  "
                  f"fast={t['fast_settling_trade_count']:>6,}  "
                  f"cats={t['counted_cats_honorable']}")
    else:
        print("  (none)")
    print()

    # ------------------------- headline numbers -------------------------
    print("HEADLINE NUMBERS")
    print("-" * 100)
    n = len(traders)
    profitable = [t for t in traders if (t.get("total_realized_pnl") or 0) > 0]
    has_any = [t for t in traders if t.get("resolved_trade_count", 0) > 0]
    print(f"  traders analyzed:                {n}")
    print(f"  with any resolved PnL data:      {len(has_any)}")
    print(f"  profitable on resolved markets:  {len(profitable)} "
          f"({100*len(profitable)/max(len(has_any),1):.1f}% of has-any)")
    if has_any:
        total_pnl = sum(t["total_realized_pnl"] or 0 for t in has_any)
        print(f"  total realized PnL (sum):        ${total_pnl:,.0f}")
        print(f"  mean PnL:                        ${total_pnl/len(has_any):,.0f}")
        print(f"  median PnL:                      "
              f"${statistics.median([t['total_realized_pnl'] or 0 for t in has_any]):,.0f}")
    wrs = [t["win_rate"] for t in has_any if t.get("win_rate") is not None]
    if wrs:
        print(f"  mean win_rate:                   {statistics.mean(wrs):.3f}")
        print(f"  median win_rate:                 {statistics.median(wrs):.3f}")


if __name__ == "__main__":
    main()
