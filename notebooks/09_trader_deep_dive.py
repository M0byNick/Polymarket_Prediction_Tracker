#!/usr/bin/env python3
"""
Track 2E — Single-trader deep dive.

Default target: 0x006cc834Cc092684F1B56626E23BEdB3835c16ea (the discretionary
multi-sport trader, $4.55M PnL, 4,485 trades, 91% sports per v2 classifier).

Can be pointed at any wallet in strict_wallets via --wallet.

Uses corrected category_v2/category_broad from strict_markets and optional
price history from strict_price_history. Produces:

  1. Per-category PnL breakdown (which sports are they actually good at?)
  2. Daily PnL trajectory (growth curve, drawdowns)
  3. Bet sizing as function of conviction (proxy: absolute distance from 0.5)
  4. Time-of-entry distribution with decile breakdown (early vs late edge)
  5. Position-level features: hold time, # trades, avg entry vs outcome
  6. Top winning / losing markets
  7. Hour-of-day / day-of-week behavior (human vs bot check)
  8. Slippage analysis (requires price history) — trader's fill vs market mid
  9. 10 testable hypotheses about their strategy
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone


DEFAULT_WALLET_HINT = "006cc834cc"


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


def resolve_wallet(conn, hint: str | None) -> tuple[str, str]:
    """Find a wallet by substring match on address or name."""
    if hint:
        h = hint.lower()
    else:
        h = DEFAULT_WALLET_HINT
    rows = conn.execute("SELECT wallet, name FROM strict_wallets").fetchall()
    for w, n in rows:
        if h in w.lower() or h in (n or "").lower():
            return w, n or w[:12]
    print(f"No wallet matching '{hint}'", file=sys.stderr)
    print("Available wallets:", file=sys.stderr)
    for w, n in rows:
        print(f"  {w[:16]}  {n}", file=sys.stderr)
    sys.exit(1)


def load_trades(conn, wallet: str) -> list:
    cur = conn.execute(
        """SELECT t.timestamp, t.condition_id, t.outcome_index, t.side, t.price,
                  t.size, t.usdc_size, t.title, t.event_slug,
                  COALESCE(m.category_v2, 'misc'), COALESCE(m.category_broad, 'misc'),
                  m.closed, m.outcome_prices, m.resolve_ts
           FROM strict_trades t
           LEFT JOIN strict_markets m ON t.condition_id = m.condition_id
           WHERE t.wallet = ?
           ORDER BY t.timestamp""",
        (wallet,),
    )
    keys = ["ts", "cid", "oi", "side", "price", "size", "usdc",
            "title", "event_slug", "cat_v2", "cat_broad",
            "closed", "outcome_prices", "resolve_ts"]
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def compute_positions(trades: list) -> dict:
    """Aggregate trades into (cid, oi) positions."""
    pos = defaultdict(lambda: {
        "shares": 0.0, "usdc": 0.0, "buys": 0, "sells": 0,
        "first_ts": None, "last_ts": None,
        "title": "", "cat_v2": "misc", "cat_broad": "misc",
        "closed": 0, "outcome_prices": None, "resolve_ts": None,
        "n_trades": 0,
    })
    for t in trades:
        key = (t["cid"], t["oi"])
        p = pos[key]
        if t["side"] == "BUY":
            p["shares"] += t["size"]
            p["usdc"] -= t["usdc"]
            p["buys"] += 1
        else:
            p["shares"] -= t["size"]
            p["usdc"] += t["usdc"]
            p["sells"] += 1
        p["n_trades"] += 1
        ts = t["ts"]
        if ts:
            if p["first_ts"] is None or ts < p["first_ts"]:
                p["first_ts"] = ts
            if p["last_ts"] is None or ts > p["last_ts"]:
                p["last_ts"] = ts
        if not p["title"]:
            p["title"] = t["title"]
            p["cat_v2"] = t["cat_v2"]
            p["cat_broad"] = t["cat_broad"]
            p["closed"] = t["closed"]
            p["outcome_prices"] = t["outcome_prices"]
            p["resolve_ts"] = t["resolve_ts"]
    return dict(pos)


def settle_pnl(p: dict, outcome_index: int) -> tuple[float | None, bool | None]:
    if not p["closed"] or not p["outcome_prices"]:
        return None, None
    try:
        prices = json.loads(p["outcome_prices"]) if isinstance(p["outcome_prices"], str) else p["outcome_prices"]
        payout = float(prices[outcome_index])
    except Exception:
        return None, None
    pnl = p["shares"] * payout + p["usdc"]
    won = payout > 0.5
    return pnl, won


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/polymarket_strict_deep.db")
    parser.add_argument("--wallet", help="Wallet address or name substring")
    parser.add_argument("--out", default="data/phase2e_deep_dive.json")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    wallet, name = resolve_wallet(conn, args.wallet)

    trades = load_trades(conn, wallet)
    n = len(trades)

    print("=" * 100)
    print(f"TRADER DEEP DIVE: {name}")
    print(f"  wallet: {wallet}")
    print(f"  trades: {n:,}")
    print("=" * 100)

    # ------------------------- daily trajectory -------------------------
    positions = compute_positions(trades)
    settled_positions = []
    for (cid, oi), p in positions.items():
        pnl, won = settle_pnl(p, oi)
        if pnl is None:
            continue
        settled_positions.append({
            "cid": cid, "oi": oi,
            "title": p["title"][:80],
            "cat_v2": p["cat_v2"],
            "cat_broad": p["cat_broad"],
            "pnl": pnl,
            "won": won,
            "n_trades": p["n_trades"],
            "first_ts": p["first_ts"],
            "last_ts": p["last_ts"],
            "resolve_ts": p["resolve_ts"],
            "net_shares": p["shares"],
            "net_usdc": p["usdc"],
        })

    total_pnl = sum(sp["pnl"] for sp in settled_positions)
    resolved = len(settled_positions)
    n_wins = sum(1 for sp in settled_positions if sp["won"])
    n_losses = resolved - n_wins
    avg_pnl = total_pnl / resolved if resolved else 0
    print(f"\n[OVERALL]")
    print(f"  settled positions: {resolved:,}  ({n_wins:,}W / {n_losses:,}L)")
    print(f"  position WR:       {n_wins/max(resolved,1):.3f}")
    print(f"  total realized:    ${total_pnl:,.0f}")
    print(f"  avg per position:  ${avg_pnl:,.0f}")
    print(f"  best position:     ${max(sp['pnl'] for sp in settled_positions):,.0f}")
    print(f"  worst position:    ${min(sp['pnl'] for sp in settled_positions):,.0f}")

    # ------------------------- by category -------------------------
    print(f"\n[CATEGORY BREAKDOWN — v2 fine-grained]")
    by_cat = defaultdict(lambda: {"n_pos": 0, "n_trades": 0, "pnl": 0.0, "wins": 0})
    for sp in settled_positions:
        c = sp["cat_v2"]
        by_cat[c]["n_pos"] += 1
        by_cat[c]["n_trades"] += sp["n_trades"]
        by_cat[c]["pnl"] += sp["pnl"]
        if sp["won"]:
            by_cat[c]["wins"] += 1

    print(f"  {'category':<28}  {'pos':>6}  {'trades':>7}  {'pnl':>13}  "
          f"{'ppp':>10}  {'wr':>6}")
    cat_sorted = sorted(by_cat.items(), key=lambda x: -x[1]["pnl"])
    for c, stats in cat_sorted[:25]:
        wr = stats["wins"] / stats["n_pos"] if stats["n_pos"] else 0
        ppp = stats["pnl"] / stats["n_pos"] if stats["n_pos"] else 0
        print(f"  {c:<28}  {stats['n_pos']:>6}  {stats['n_trades']:>7}  "
              f"${stats['pnl']:>11,.0f}  ${ppp:>8,.0f}  {wr:>5.3f}")

    # ------------------------- daily PnL trajectory -------------------------
    print(f"\n[DAILY PNL TRAJECTORY]")
    daily_pnl = defaultdict(float)
    for sp in settled_positions:
        if sp["resolve_ts"]:
            d = datetime.fromtimestamp(sp["resolve_ts"], timezone.utc).date().isoformat()
            daily_pnl[d] += sp["pnl"]

    sorted_days = sorted(daily_pnl.items())
    running = 0.0
    max_cum = 0.0
    max_dd = 0.0
    max_dd_duration = 0
    current_dd_start = None
    current_dd_duration = 0
    all_curve = []
    for d, pnl in sorted_days:
        running += pnl
        all_curve.append((d, pnl, running))
        if running > max_cum:
            max_cum = running
            current_dd_start = None
            current_dd_duration = 0
        else:
            dd = max_cum - running
            if dd > max_dd:
                max_dd = dd
            if current_dd_start is None:
                current_dd_start = d
                current_dd_duration = 1
            else:
                current_dd_duration += 1
                if current_dd_duration > max_dd_duration:
                    max_dd_duration = current_dd_duration

    if sorted_days:
        print(f"  trading days:      {len(sorted_days)}")
        winning_days = sum(1 for _, p, _ in all_curve if p > 0)
        losing_days = sum(1 for _, p, _ in all_curve if p < 0)
        flat_days = len(sorted_days) - winning_days - losing_days
        print(f"  winning days:      {winning_days} ({100*winning_days/len(sorted_days):.1f}%)")
        print(f"  losing days:       {losing_days} ({100*losing_days/len(sorted_days):.1f}%)")
        print(f"  flat days:         {flat_days}")
        print(f"  best day:          ${max(p for _, p, _ in all_curve):,.0f}")
        print(f"  worst day:         ${min(p for _, p, _ in all_curve):,.0f}")
        print(f"  max drawdown:      ${max_dd:,.0f}")
        print(f"  max DD duration:   {max_dd_duration} days")
        # Print the curve every ~10% of days
        step = max(len(sorted_days) // 12, 1)
        print(f"\n  curve samples (every {step} days):")
        for i in range(0, len(all_curve), step):
            d, pnl, cum = all_curve[i]
            marker = "↑" if pnl > 0 else ("↓" if pnl < 0 else "=")
            print(f"    {d}  day_pnl=${pnl:>10,.0f}  cum=${cum:>12,.0f}  {marker}")

    # ------------------------- sizing as function of conviction -------------------------
    print(f"\n[SIZING VS CONVICTION]")
    # Buy-side trades only; conviction proxy = abs(entry price - 0.5) (distance from coin-flip)
    buys = [t for t in trades if t["side"] == "BUY" and t["price"] is not None and t["usdc"] > 0]
    conviction_buckets = {
        "0.45-0.55 (toss-up)":  [],
        "0.35-0.45 or 0.55-0.65": [],
        "0.20-0.35 or 0.65-0.80": [],
        "0.05-0.20 or 0.80-0.95": [],
        "0.00-0.05 or 0.95-1.00": [],
    }
    for t in buys:
        dist = abs(t["price"] - 0.5)
        if dist < 0.05:
            k = "0.45-0.55 (toss-up)"
        elif dist < 0.15:
            k = "0.35-0.45 or 0.55-0.65"
        elif dist < 0.30:
            k = "0.20-0.35 or 0.65-0.80"
        elif dist < 0.45:
            k = "0.05-0.20 or 0.80-0.95"
        else:
            k = "0.00-0.05 or 0.95-1.00"
        conviction_buckets[k].append(t["usdc"])

    print(f"  {'bucket':<32}  {'n':>7}  {'median':>10}  {'mean':>10}  {'p90':>10}")
    for label, vals in conviction_buckets.items():
        if not vals:
            print(f"  {label:<32}  {len(vals):>7}  (none)")
            continue
        med = statistics.median(vals)
        mean = sum(vals) / len(vals)
        p90 = percentile(vals, 90)
        print(f"  {label:<32}  {len(vals):>7}  ${med:>8,.0f}  ${mean:>8,.0f}  ${p90:>8,.0f}")

    # ------------------------- time-of-entry distribution -------------------------
    print(f"\n[TIME-OF-ENTRY DISTRIBUTION (hours before resolution)]")
    hours_buckets = {"<1h": 0, "1-6h": 0, "6-24h": 0, "1-3d": 0, "3-7d": 0, ">7d": 0}
    hours_pnl = {k: 0.0 for k in hours_buckets}
    # Compute per-position avg entry time
    for sp in settled_positions:
        if not sp["first_ts"] or not sp["resolve_ts"]:
            continue
        h = (sp["resolve_ts"] - sp["first_ts"]) / 3600
        if h < 1:
            k = "<1h"
        elif h < 6:
            k = "1-6h"
        elif h < 24:
            k = "6-24h"
        elif h < 72:
            k = "1-3d"
        elif h < 168:
            k = "3-7d"
        else:
            k = ">7d"
        hours_buckets[k] += 1
        hours_pnl[k] += sp["pnl"]

    print(f"  {'bucket':<10}  {'positions':>10}  {'total_pnl':>14}  {'ppp':>10}")
    for k in ["<1h", "1-6h", "6-24h", "1-3d", "3-7d", ">7d"]:
        n_b = hours_buckets[k]
        pnl = hours_pnl[k]
        ppp = pnl / n_b if n_b else 0
        print(f"  {k:<10}  {n_b:>10}  ${pnl:>12,.0f}  ${ppp:>8,.0f}")

    # ------------------------- top winning / losing positions -------------------------
    print(f"\n[TOP 10 WINNING POSITIONS]")
    sorted_by_pnl = sorted(settled_positions, key=lambda x: -x["pnl"])
    for sp in sorted_by_pnl[:10]:
        print(f"  ${sp['pnl']:>11,.0f}  {sp['cat_v2']:<26}  {sp['title'][:50]}")

    print(f"\n[TOP 10 LOSING POSITIONS]")
    for sp in sorted_by_pnl[-10:]:
        print(f"  ${sp['pnl']:>11,.0f}  {sp['cat_v2']:<26}  {sp['title'][:50]}")

    # ------------------------- day of week / hour of day -------------------------
    print(f"\n[DAY OF WEEK PATTERN (trades)]")
    dow_counter = Counter()
    hod_counter = Counter()
    for t in trades:
        if not t["ts"]:
            continue
        dt = datetime.fromtimestamp(t["ts"], timezone.utc)
        dow_counter[dt.strftime("%a")] += 1
        hod_counter[dt.hour] += 1
    for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
        pct = 100 * dow_counter.get(d, 0) / n if n else 0
        print(f"  {d}  {dow_counter.get(d, 0):>6,}  ({pct:>5.1f}%)")

    print(f"\n[HOUR OF DAY (UTC)]")
    for h in range(0, 24, 2):
        c1 = hod_counter.get(h, 0)
        c2 = hod_counter.get(h + 1, 0)
        total = c1 + c2
        bar = "#" * int(total / n * 200) if n else ""
        print(f"  {h:02d}-{h+1:02d}  {total:>6,}  {bar}")

    # ------------------------- slippage analysis (if price history available) -------------------------
    has_price_history = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='strict_price_history'"
    ).fetchone()[0]

    slippage_samples = []
    if has_price_history:
        # For each buy-side trade, look up market mid-price at trade timestamp.
        # Compare trader's fill price to market mid.
        price_lookup_count = 0
        for t in trades[:2000]:  # cap to 2000 for speed
            if t["side"] != "BUY":
                continue
            # Find nearest price history point for this token
            cur = conn.execute(
                """SELECT price FROM strict_price_history
                   WHERE condition_id = ? AND ts <= ?
                   ORDER BY ts DESC LIMIT 1""",
                (t["cid"], t["ts"]),
            ).fetchone()
            if not cur:
                continue
            market_mid = cur[0]
            slippage_bps = (t["price"] - market_mid) * 10000
            slippage_samples.append(slippage_bps)
            price_lookup_count += 1

        if slippage_samples:
            print(f"\n[SLIPPAGE VS MARKET MID (first 2k buys with price history)]")
            print(f"  samples: {len(slippage_samples)}")
            print(f"  median:  {statistics.median(slippage_samples):.1f} bps")
            print(f"  mean:    {sum(slippage_samples)/len(slippage_samples):.1f} bps")
            print(f"  p25:     {percentile(slippage_samples, 25):.1f} bps")
            print(f"  p75:     {percentile(slippage_samples, 75):.1f} bps")
            print(f"  p90:     {percentile(slippage_samples, 90):.1f} bps")
            pos_slip = sum(1 for s in slippage_samples if s > 0)
            neg_slip = sum(1 for s in slippage_samples if s < 0)
            print(f"  paid above mid: {pos_slip} ({100*pos_slip/len(slippage_samples):.0f}%)")
            print(f"  paid below mid: {neg_slip} ({100*neg_slip/len(slippage_samples):.0f}%)")
        else:
            print(f"\n[SLIPPAGE] no price history lookups succeeded — may need to run Track 2B first")

    # ------------------------- testable hypotheses -------------------------
    print(f"\n[TESTABLE HYPOTHESES — derived from observed patterns]")
    hypotheses = []
    cat_sorted_pnl = sorted(by_cat.items(), key=lambda x: -x[1]["pnl"])
    if cat_sorted_pnl:
        top_cat, top_stats = cat_sorted_pnl[0]
        hypotheses.append(
            f"H1: Their edge is concentrated in {top_cat} (${top_stats['pnl']:,.0f} "
            f"of ${total_pnl:,.0f} = {100*top_stats['pnl']/max(total_pnl,1):.0f}% from "
            f"{top_stats['n_pos']} positions)"
        )

    # Hours-before concentration
    max_hours_bucket = max(hours_pnl.items(), key=lambda x: x[1]) if hours_pnl else None
    if max_hours_bucket and max_hours_bucket[1] > 0:
        hypotheses.append(
            f"H2: Their edge is strongest when entering {max_hours_bucket[0]} "
            f"before resolution (${max_hours_bucket[1]:,.0f} cumulative PnL in this bucket)"
        )

    # Sizing bucket edge
    max_sizing = max(
        (k for k, v in conviction_buckets.items() if v),
        key=lambda k: sum(conviction_buckets[k]) / len(conviction_buckets[k]),
        default=None,
    )
    if max_sizing:
        hypotheses.append(
            f"H3: They size largest in the {max_sizing} conviction bucket "
            f"(mean ${sum(conviction_buckets[max_sizing])/len(conviction_buckets[max_sizing]):.0f})"
        )

    # Time-of-day
    if hod_counter:
        peak_h = max(hod_counter.items(), key=lambda x: x[1])[0]
        hypotheses.append(
            f"H4: They are active primarily around {peak_h:02d}:00 UTC "
            f"(suggests {['Asia', 'Asia', 'Asia', 'Asia', 'Asia', 'Asia', 'Europe', 'Europe', 'Europe', 'Europe', 'Europe', 'Europe', 'Europe/Americas', 'Americas', 'Americas', 'Americas', 'Americas', 'Americas', 'Americas', 'Americas', 'Americas/Asia', 'Asia', 'Asia', 'Asia'][peak_h]} timezone)"
        )

    # Position count vs PnL skew
    if settled_positions:
        big_wins = sorted(settled_positions, key=lambda x: -x["pnl"])[:10]
        big_win_pnl = sum(sp["pnl"] for sp in big_wins)
        hypotheses.append(
            f"H5: Top 10 positions drive ${big_win_pnl:,.0f} / {100*big_win_pnl/max(total_pnl,1):.0f}% "
            f"of total PnL (skewness check)"
        )

    for h in hypotheses:
        print(f"  {h}")

    # Save full report
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "wallet": wallet,
        "name": name,
        "n_trades": n,
        "n_positions": len(positions),
        "n_settled": resolved,
        "total_pnl": total_pnl,
        "win_rate": n_wins / max(resolved, 1),
        "by_category": {c: dict(v) for c, v in by_cat.items()},
        "hours_buckets": hours_buckets,
        "hours_pnl": hours_pnl,
        "daily_curve": all_curve,
        "max_drawdown": max_dd,
        "max_dd_duration_days": max_dd_duration,
        "hypotheses": hypotheses,
        "has_slippage_data": bool(slippage_samples),
        "slippage_samples_median_bps": (
            statistics.median(slippage_samples) if slippage_samples else None
        ),
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  report → {args.out}")


if __name__ == "__main__":
    main()
