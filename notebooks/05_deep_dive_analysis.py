#!/usr/bin/env python3
"""
Phase 1D — Deep dive analysis of the 17 strict survivors.

Reads data/polymarket_strict_deep.db (populated by Phase 1A) and produces
the feature-extraction report we're actually here for. For each strict
survivor computes:

  1. Sub-category breakdown (NBA / NFL / MLB / NHL / soccer leagues / tennis /
     F1 / UFC / misc sports / crypto / weather / other)
  2. Entry price distribution (favorite/underdog/balanced classification)
  3. Time-of-entry distribution (hours-before-resolution)
  4. Position sizing (USDC amount per trade: flat, variable, martingale)
  5. Hold time distribution
  6. Day-of-week + hour-of-day patterns (human vs bot)
  7. Edge profile classification (A=high-WR specialist, B=volume coin-flip,
     C=underdog, X=mid-range)

And across all survivors:
  - Market overlap matrix (who trades the same markets?)
  - Category concentration per trader (pure specialist vs generalist)
  - Portfolio-level summary

Output: stdout text report + optional JSON dump at data/phase1d_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

# --------------- sub-category classification ---------------

SUBCAT_RULES = [
    # (subcategory, regex pattern compiled against title+slug+event_slug)
    ("nba",       r"\b(nba|lakers|celtics|warriors|heat|knicks|nuggets|mavericks|sixers|bucks|suns|clippers|grizzlies|bulls|pelicans|kings|rockets|timberwolves|thunder|magic|hornets|wizards|hawks|pistons|cavaliers|nets|blazers|jazz|spurs|raptors|pacers)\b"),
    ("nfl",       r"\b(nfl|chiefs|eagles|cowboys|49ers|bills|ravens|packers|steelers|patriots|giants|jets|dolphins|bears|vikings|lions|cardinals|rams|seahawks|falcons|panthers|saints|bengals|browns|texans|titans|colts|jaguars|raiders|chargers|broncos)\b"),
    ("mlb",       r"\b(mlb|yankees|dodgers|red sox|astros|braves|phillies|mets|cubs|cardinals|giants|padres|blue jays|rangers|orioles|rays|guardians|twins|tigers|royals|white sox|mariners|angels|athletics|brewers|reds|pirates|marlins|nationals|rockies|diamondbacks)\b"),
    ("nhl",       r"\b(nhl|maple leafs|canadiens|rangers-.*bruins|panthers|oilers|stars|avalanche|golden knights|lightning|hurricanes|flyers|islanders|devils|blackhawks|red wings|penguins|senators|jets|sharks|ducks|kings|predators|wild|blues|flames|canucks)\b"),
    ("soccer_epl",     r"\b(manchester (city|united)|arsenal|liverpool|chelsea|tottenham|newcastle|aston villa|west ham|brighton|crystal palace|fulham|brentford|wolves|everton|bournemouth|nottingham forest|sheffield|luton|burnley|premier league)\b"),
    ("soccer_laliga",  r"\b(real madrid|barcelona|atletico|sevilla|villarreal|real sociedad|valencia|athletic bilbao|getafe|osasuna|betis|girona|rayo|mallorca|celta|la liga)\b"),
    ("soccer_seriea",  r"\b(juventus|inter |ac milan|napoli|roma|lazio|atalanta|fiorentina|bologna|torino|udinese|monza|genoa|lecce|serie a)\b"),
    ("soccer_j",       r"\b(j-league|j1|j2|kyoto sanga|fagiano|vissel|nagoya grampus|urawa|kashima)\b"),
    ("soccer_mls",     r"\b(mls|inter miami|lafc|seattle sounders|atlanta united|portland timbers|cincinnati|columbus crew|philadelphia union|nashville sc|new england|orlando city|nycfc|red bulls|houston dynamo|chicago fire|toronto fc|whitecaps|real salt lake|dc united|dallas|minnesota|austin|colorado rapids|montreal)\b"),
    ("soccer_other",   r"\b(bundesliga|ligue 1|uefa|champions league|europa league|copa)\b"),
    ("tennis",         r"\b(wimbledon|roland garros|australian open|us open|atp|wta|djokovic|alcaraz|sinner|medvedev|swiatek|gauff|sabalenka|rublev)\b"),
    ("f1",             r"\b(formula 1|f1|grand prix|verstappen|hamilton|norris|leclerc|russell|piastri|bortoleto|lindblad)\b"),
    ("ufc_mma",        r"\b(ufc|mma|jon jones|khabib)\b"),
    ("golf",           r"\b(masters|pga|us open golf|british open|liv golf)\b"),
    ("cricket",        r"\b(cricket|ipl|test match)\b"),
    ("olympics",       r"\bolympic"),
    ("ou_total",       r"\b(o/u|over/under|total|-1\.5|-2\.5|\+1\.5|\+2\.5)\b"),
    ("moneyline",      r"\b(moneyline|ml)\b"),
    # non-sports
    ("crypto",         r"\b(bitcoin|btc|ethereum|eth|solana|sol\b)"),
    ("weather",        r"\b(temperature|high temp|low temp|rainfall|snowfall|°f|°c)\b"),
    ("election",       r"\b(election|president|senate|house|primary|trump|biden|harris|vance|governor)\b"),
    ("news",           r"\b(announced|announcement|ceasefire|summit|resignation|impeach)\b"),
]


def classify_subcategory(title: str, slug: str, event_slug: str) -> str:
    blob = " ".join(filter(None, [title, slug, event_slug])).lower()
    for subcat, pat in SUBCAT_RULES:
        if re.search(pat, blob):
            return subcat
    return "misc"


# --------------- helpers ---------------

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


def describe(vals, label: str, fmt: str = "{:.2f}") -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return f"{label}: (no data)"
    n = len(vals)
    return (f"{label}: n={n:,} "
            f"min={fmt.format(min(vals))} "
            f"p25={fmt.format(percentile(vals, 25))} "
            f"p50={fmt.format(percentile(vals, 50))} "
            f"p75={fmt.format(percentile(vals, 75))} "
            f"p90={fmt.format(percentile(vals, 90))} "
            f"max={fmt.format(max(vals))} "
            f"mean={fmt.format(sum(vals)/n)}")


# --------------- per-trader analysis ---------------

def analyze_trader(conn, wallet: str, name: str) -> dict:
    report = {"wallet": wallet, "name": name}

    # Pull all trades for this wallet
    trades = conn.execute(
        """SELECT timestamp, condition_id, outcome_index, side, price, size, usdc_size,
                  outcome, title, slug, event_slug
           FROM strict_trades WHERE wallet = ?""",
        (wallet,),
    ).fetchall()

    if not trades:
        report["error"] = "no trades in DB"
        return report

    n_trades = len(trades)
    report["n_trades"] = n_trades

    # --- Sub-category breakdown ---
    subcat_counts = Counter()
    for t in trades:
        title, slug, ev_slug = t[8] or "", t[9] or "", t[10] or ""
        sub = classify_subcategory(title, slug, ev_slug)
        subcat_counts[sub] += 1
    report["subcategories"] = dict(subcat_counts.most_common())

    # --- Entry price distribution (BUY side only) ---
    buy_prices = [t[4] for t in trades if t[3] == "BUY" and t[4] is not None]
    sell_prices = [t[4] for t in trades if t[3] == "SELL" and t[4] is not None]
    report["entry_price"] = {
        "n_buys": len(buy_prices),
        "n_sells": len(sell_prices),
        "p10": percentile(buy_prices, 10),
        "p25": percentile(buy_prices, 25),
        "p50": percentile(buy_prices, 50),
        "p75": percentile(buy_prices, 75),
        "p90": percentile(buy_prices, 90),
        "mean": sum(buy_prices) / len(buy_prices) if buy_prices else None,
    }

    # Favorite / underdog / balanced classifier
    ep_p50 = report["entry_price"]["p50"]
    if ep_p50 is None:
        report["price_profile"] = "unknown"
    elif ep_p50 >= 0.70:
        report["price_profile"] = "favorite_heavy"
    elif ep_p50 <= 0.30:
        report["price_profile"] = "underdog_heavy"
    elif ep_p50 >= 0.60:
        report["price_profile"] = "favorite_tilt"
    elif ep_p50 <= 0.40:
        report["price_profile"] = "underdog_tilt"
    else:
        report["price_profile"] = "balanced"

    # --- Position size (USDC per trade) ---
    usdc_sizes = [t[6] for t in trades if t[6] is not None and t[6] > 0]
    report["usdc_size"] = {
        "n": len(usdc_sizes),
        "p10": percentile(usdc_sizes, 10),
        "p25": percentile(usdc_sizes, 25),
        "p50": percentile(usdc_sizes, 50),
        "p75": percentile(usdc_sizes, 75),
        "p90": percentile(usdc_sizes, 90),
        "max": max(usdc_sizes) if usdc_sizes else None,
        "mean": sum(usdc_sizes) / len(usdc_sizes) if usdc_sizes else None,
    }
    if usdc_sizes and len(usdc_sizes) > 10:
        # Coefficient of variation on usdc_size (tells us flat vs variable)
        mean_sz = sum(usdc_sizes) / len(usdc_sizes)
        std_sz = statistics.stdev(usdc_sizes) if len(usdc_sizes) > 1 else 0
        cv = std_sz / mean_sz if mean_sz > 0 else None
        report["sizing_cv"] = round(cv, 3) if cv is not None else None
        if cv is not None:
            if cv < 0.3:
                report["sizing_profile"] = "flat_bet"
            elif cv < 1.0:
                report["sizing_profile"] = "moderate_variable"
            else:
                report["sizing_profile"] = "highly_variable"

    # --- Time-of-entry (hours before resolution) ---
    # Requires join on strict_markets.resolve_ts
    cid_to_resolve = dict(conn.execute(
        """SELECT condition_id, resolve_ts
           FROM strict_markets
           WHERE condition_id IN (SELECT DISTINCT condition_id FROM strict_trades WHERE wallet = ?)
           AND resolve_ts IS NOT NULL""",
        (wallet,),
    ).fetchall())

    hours_before = []
    for t in trades:
        ts, cid = t[0], t[1]
        resolve_ts = cid_to_resolve.get(cid)
        if resolve_ts and ts and resolve_ts > ts:
            hours_before.append((resolve_ts - ts) / 3600)

    report["hours_before_resolution"] = {
        "n": len(hours_before),
        "p10": percentile(hours_before, 10),
        "p25": percentile(hours_before, 25),
        "p50": percentile(hours_before, 50),
        "p75": percentile(hours_before, 75),
        "p90": percentile(hours_before, 90),
        "mean": sum(hours_before) / len(hours_before) if hours_before else None,
    }

    # --- Day-of-week / hour-of-day patterns ---
    dow_counts = Counter()
    hod_counts = Counter()
    for t in trades:
        ts = t[0]
        if not ts:
            continue
        dt = datetime.fromtimestamp(ts, timezone.utc)
        dow_counts[dt.weekday()] += 1
        hod_counts[dt.hour] += 1
    report["day_of_week"] = dict(dow_counts)
    report["hour_of_day"] = dict(hod_counts)

    # --- Overall span ---
    ts_list = [t[0] for t in trades if t[0]]
    if ts_list:
        earliest, latest = min(ts_list), max(ts_list)
        report["earliest"] = datetime.fromtimestamp(earliest, timezone.utc).date().isoformat()
        report["latest"] = datetime.fromtimestamp(latest, timezone.utc).date().isoformat()
        days = (latest - earliest) / 86400
        report["days_active"] = round(days, 1)
        report["trades_per_day_mean"] = round(n_trades / max(days, 1), 1)

    return report


# --------------- cross-trader analysis ---------------

def market_overlap(conn, wallets: list) -> dict:
    """
    For each pair of wallets, count the number of markets both traded.
    Returns: {(w1, w2): count} for upper triangle only.
    """
    wallet_markets = {}
    for w in wallets:
        cur = conn.execute(
            "SELECT DISTINCT condition_id FROM strict_trades WHERE wallet = ?",
            (w,),
        )
        wallet_markets[w] = set(r[0] for r in cur.fetchall())

    overlap = {}
    ws = sorted(wallets)
    for i, w1 in enumerate(ws):
        for w2 in ws[i + 1:]:
            inter = wallet_markets[w1] & wallet_markets[w2]
            if inter:
                overlap[(w1, w2)] = len(inter)
    return overlap


# --------------- main ---------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/polymarket_strict_deep.db")
    parser.add_argument("--out", default="data/phase1d_report.json")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)

    wallets = conn.execute(
        "SELECT wallet, name FROM strict_wallets ORDER BY step2_realized_pnl DESC"
    ).fetchall()

    print("=" * 100)
    print(f"PHASE 1D — deep dive on {len(wallets)} strict survivors")
    print("=" * 100)

    # Per-trader
    all_reports = []
    for wallet, name in wallets:
        r = analyze_trader(conn, wallet, name)
        all_reports.append(r)

        n = r.get("n_trades", 0)
        if not n:
            print(f"\n{'―' * 100}\n{name} ({wallet[:12]}) — NO DATA")
            continue

        print(f"\n{'―' * 100}")
        print(f"{name} ({wallet[:12]})  —  {n:,} trades  {r.get('earliest','?')} → {r.get('latest','?')}  "
              f"({r.get('days_active','?')}d, {r.get('trades_per_day_mean','?')}/day)")
        print(f"{'―' * 100}")

        # Sub-category top 5
        print("  sub-categories (top 6):")
        for sub, cnt in list(r["subcategories"].items())[:6]:
            pct = 100 * cnt / n
            print(f"    {sub:<16} {cnt:>7,}  ({pct:>5.1f}%)")

        # Price profile
        ep = r["entry_price"]
        if ep["p50"] is not None:
            print(f"  entry price (buy):  p25={ep['p25']:.3f}  p50={ep['p50']:.3f}  "
                  f"p75={ep['p75']:.3f}  mean={ep['mean']:.3f}  → {r['price_profile']}")

        # Sizing
        sz = r.get("usdc_size", {})
        if sz.get("p50") is not None:
            print(f"  usdc size:          p25=${sz['p25']:.0f}  p50=${sz['p50']:.0f}  "
                  f"p75=${sz['p75']:.0f}  p90=${sz['p90']:.0f}  max=${sz['max']:.0f}  "
                  f"cv={r.get('sizing_cv','-')}  → {r.get('sizing_profile','?')}")

        # Hours before resolution
        hb = r["hours_before_resolution"]
        if hb["p50"] is not None:
            print(f"  hours before resolve: p10={hb['p10']:.1f}h p25={hb['p25']:.1f}h "
                  f"p50={hb['p50']:.1f}h p75={hb['p75']:.1f}h p90={hb['p90']:.1f}h "
                  f"mean={hb['mean']:.1f}h")

        # Hour-of-day pattern (collapsed to buckets)
        hod = r.get("hour_of_day", {})
        if hod:
            total = sum(hod.values())
            buckets = {
                "asia_night":     sum(hod.get(h, 0) for h in range(16, 24)),   # 16-23 UTC = evening in Asia
                "americas_day":   sum(hod.get(h, 0) for h in range(13, 24)),    # 13-23 UTC = daytime in Americas (overlap)
                "europe_day":     sum(hod.get(h, 0) for h in range(7, 19)),     # 7-18 UTC = daytime in Europe
                "americas_night": sum(hod.get(h, 0) for h in range(0, 12)),     # 0-11 UTC = night in Americas
            }
            top = max(buckets.items(), key=lambda x: x[1])
            print(f"  busiest UTC block:  {top[0]} ({100*top[1]/total:.0f}% of trades)")

    # Cross-trader: market overlap
    print("\n" + "=" * 100)
    print("MARKET OVERLAP — pairs of strict survivors that traded the same markets")
    print("=" * 100)
    overlap = market_overlap(conn, [w for w, _ in wallets])
    sorted_overlap = sorted(overlap.items(), key=lambda x: -x[1])
    name_by_wallet = {w: n for w, n in wallets}
    for (w1, w2), count in sorted_overlap[:25]:
        n1 = name_by_wallet.get(w1, w1[:12])[:18]
        n2 = name_by_wallet.get(w2, w2[:12])[:18]
        print(f"  {n1:<18} × {n2:<18}  {count:>5} markets")

    # Category aggregate
    print("\n" + "=" * 100)
    print("CATEGORY CONCENTRATION — % of each trader's trades in their #1 sub-category")
    print("=" * 100)
    concentration = []
    for r in all_reports:
        if not r.get("subcategories"):
            continue
        n = r["n_trades"]
        top_sub, top_n = next(iter(r["subcategories"].items()))
        concentration.append((r["name"], top_sub, top_n, n, 100 * top_n / n))
    concentration.sort(key=lambda x: -x[4])
    for name, sub, cnt, n, pct in concentration:
        print(f"  {name[:26]:<26}  {sub:<16} {cnt:>7,}/{n:>7,}  ({pct:>5.1f}%)")

    # Save report
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "db": args.db,
            "reports": all_reports,
            "market_overlap": {f"{w1[:10]}_{w2[:10]}": c for (w1, w2), c in overlap.items()},
        }, f, indent=2, default=str)
    print(f"\nreport → {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
