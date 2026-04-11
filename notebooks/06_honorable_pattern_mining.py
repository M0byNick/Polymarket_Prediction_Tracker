#!/usr/bin/env python3
"""
Phase 1E — Pattern mining on the honorable-tier population (119 traders).

Uses only the aggregated stats from Step 2 final summary (no additional API
pulls). Goal is to characterize the *distribution* of edge profiles in the
broader population and flag candidates that look interesting enough to
warrant a future full-history pull.

Outputs:
  - Distribution of (category mix, WR, PnL per trade, trade count)
    across honorable-tier
  - Edge profile classification (based on stats alone)
  - Top honorable traders to consider adding to deeper analysis later
  - Honorable tier comparison to strict tier (are they fundamentally different?)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict


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


def dist_line(label: str, vals, fmt: str = "{:.2f}"):
    vals = [v for v in vals if v is not None]
    if not vals:
        print(f"  {label:<24}  (no data)")
        return
    print(f"  {label:<24}  n={len(vals):>4}  "
          f"p25={fmt.format(percentile(vals,25)):>12}  "
          f"p50={fmt.format(percentile(vals,50)):>12}  "
          f"p75={fmt.format(percentile(vals,75)):>12}  "
          f"p90={fmt.format(percentile(vals,90)):>12}  "
          f"max={fmt.format(max(vals)):>12}")


def classify_edge_profile(t: dict) -> str:
    """
    Heuristic edge-profile classification from aggregate stats alone.
    """
    wr = t.get("win_rate")
    n_fast = t.get("fast_settling_trade_count", 0)
    pnl_per_trade = t.get("pnl_per_trade")
    if wr is None or n_fast is None:
        return "unknown"

    if wr >= 0.80:
        return "high_wr_favorite"
    if wr <= 0.40 and t.get("total_realized_pnl", 0) > 0:
        return "underdog_specialist"
    if 0.48 <= wr <= 0.55 and n_fast >= 5000:
        return "coinflip_volume"
    if wr >= 0.60 and n_fast >= 1000:
        return "selective_favorite"
    if 0.55 <= wr <= 0.65:
        return "mid_range"
    return "unclassified"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", default="data/polymarket_step2_final_summary.json")
    args = parser.parse_args()

    with open(args.summary) as f:
        data = json.load(f)

    traders = data["traders"]

    strict = [t for t in traders if t["passes_strict"]]
    honor_all = [t for t in traders if t["passes_honorable"]]
    honor_only = [t for t in traders if t["passes_honorable"] and not t["passes_strict"]]
    rest = [t for t in traders if not t["passes_honorable"]]

    print("=" * 100)
    print("PHASE 1E — honorable tier pattern mining")
    print("=" * 100)
    print(f"  strict survivors:       {len(strict)}")
    print(f"  honorable (all):        {len(honor_all)}  (includes strict)")
    print(f"  honorable-only:         {len(honor_only)}")
    print(f"  not honorable:          {len(rest)}")
    print(f"  total characterized:    {len(traders)}")
    print()

    # ------------------------- distribution comparison -------------------------
    print("DISTRIBUTIONS — strict vs honorable-only vs not-honorable")
    print("-" * 100)

    def print_group(group, label):
        print(f"\n  [{label}] n={len(group)}")
        dist_line("total_trades", [t["total_trades"] for t in group], "{:,.0f}")
        dist_line("fast_settling", [t["fast_settling_trade_count"] for t in group], "{:,.0f}")
        dist_line("total_pnl", [t["total_realized_pnl"] for t in group], "${:,.0f}")
        dist_line("pnl_per_trade", [t.get("pnl_per_trade") for t in group], "${:.2f}")
        dist_line("win_rate", [t.get("win_rate") for t in group], "{:.3f}")
        dist_line("days_trading", [t["days_trading"] for t in group], "{:.1f}")

    print_group(strict, "STRICT")
    print_group(honor_only, "HONORABLE-ONLY")
    print_group(rest, "NOT-HONORABLE")

    # ------------------------- edge profile distribution -------------------------
    print("\n" + "=" * 100)
    print("EDGE PROFILE DISTRIBUTION (heuristic from aggregate stats)")
    print("=" * 100)

    strict_profiles = Counter(classify_edge_profile(t) for t in strict)
    honor_profiles = Counter(classify_edge_profile(t) for t in honor_only)

    profiles = sorted(set(strict_profiles.keys()) | set(honor_profiles.keys()))
    print(f"\n  {'profile':<22}  {'strict':>8}  {'hon_only':>9}")
    for p in profiles:
        print(f"  {p:<22}  {strict_profiles.get(p, 0):>8}  {honor_profiles.get(p, 0):>9}")

    # ------------------------- category concentration -------------------------
    print("\n" + "=" * 100)
    print("CATEGORY MIX")
    print("=" * 100)

    def cat_pct(group, label):
        cats = defaultdict(int)
        for t in group:
            for c, n in (t.get("category_fast_settling_counts") or {}).items():
                cats[c] += n
        total = sum(cats.values()) or 1
        print(f"\n  [{label}] n_traders={len(group)}  fast_settling_total={total:,}")
        for c in sorted(cats.keys(), key=lambda c: -cats[c]):
            print(f"    {c:<10} {cats[c]:>10,}  ({100*cats[c]/total:>5.1f}%)")

    cat_pct(strict, "STRICT")
    cat_pct(honor_only, "HONORABLE-ONLY")
    cat_pct(rest, "NOT-HONORABLE")

    # ------------------------- honorable candidates to promote -------------------------
    print("\n" + "=" * 100)
    print("TOP HONORABLE-ONLY CANDIDATES TO DEEP-DIVE LATER")
    print("=" * 100)
    print("(Ranked by total_realized_pnl × bootstrap_pvalue inverse × sample size)")
    print()

    def promote_score(t):
        pnl = t.get("total_realized_pnl") or 0
        if pnl <= 0:
            return -1
        pv = t.get("bootstrap_pvalue")
        if pv is None:
            pv_score = 0.5
        elif pv < 0.1:
            pv_score = 1.0
        elif pv < 0.2:
            pv_score = 0.5
        else:
            pv_score = 0.2
        fs = t.get("fast_settling_trade_count") or 0
        sample_score = min(1.0, fs / 1000)
        return pnl * pv_score * sample_score

    scored = [(promote_score(t), t) for t in honor_only]
    scored = sorted([(s, t) for s, t in scored if s > 0], key=lambda x: -x[0])

    print(f"  {'rank':<5}  {'name':<28}  {'pnl':>13}  {'wr':>6}  {'fast':>7}  "
          f"{'ppt':>9}  {'p-value':>8}  profile")
    for rank, (score, t) in enumerate(scored[:25], 1):
        name = (t["name"] or t["wallet"][:12])[:28]
        pnl = t.get("total_realized_pnl") or 0
        wr = t.get("win_rate") or 0
        fs = t.get("fast_settling_trade_count") or 0
        ppt = t.get("pnl_per_trade")
        pv = t.get("bootstrap_pvalue")
        profile = classify_edge_profile(t)
        ppt_s = f"${ppt:.2f}" if ppt is not None else "-"
        pv_s = f"{pv:.3f}" if pv is not None else "-"
        print(f"  {rank:<5}  {name:<28}  ${pnl:>12,.0f}  {wr:.3f}  "
              f"{fs:>7,}  {ppt_s:>9}  {pv_s:>8}  {profile}")

    # ------------------------- strict vs honorable head-to-head -------------------------
    print("\n" + "=" * 100)
    print("WHAT MAKES STRICT DIFFERENT FROM HONORABLE-ONLY?")
    print("=" * 100)
    if strict and honor_only:
        def mean(vals): return sum(vals)/len(vals) if vals else 0
        def med(vals): return percentile(vals, 50) if vals else 0
        metrics = [
            ("mean total_trades", lambda g: mean([t["total_trades"] for t in g])),
            ("mean fast_settling", lambda g: mean([t["fast_settling_trade_count"] for t in g])),
            ("mean realized_pnl", lambda g: mean([t["total_realized_pnl"] for t in g])),
            ("median realized_pnl", lambda g: med([t["total_realized_pnl"] for t in g])),
            ("mean win_rate", lambda g: mean([t["win_rate"] for t in g if t.get("win_rate") is not None])),
            ("mean pnl_per_trade", lambda g: mean([t["pnl_per_trade"] for t in g if t.get("pnl_per_trade") is not None])),
            ("mean days_trading", lambda g: mean([t["days_trading"] for t in g])),
        ]
        print(f"\n  {'metric':<24}  {'strict':>14}  {'honorable_only':>16}  {'strict/hon':>12}")
        for label, fn in metrics:
            s_val = fn(strict)
            h_val = fn(honor_only)
            ratio = s_val / h_val if h_val else float("inf")
            s_str = f"${s_val:,.0f}" if "pnl" in label else f"{s_val:,.2f}"
            h_str = f"${h_val:,.0f}" if "pnl" in label else f"{h_val:,.2f}"
            print(f"  {label:<24}  {s_str:>14}  {h_str:>16}  {ratio:>11.2f}×")

    return 0


if __name__ == "__main__":
    sys.exit(main())
