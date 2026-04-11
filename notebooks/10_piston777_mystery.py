#!/usr/bin/env python3
"""
Phase 3a — Resolve the piston777 math mystery.

Phase 2E found: piston777 has 79% position WR with median entry price 0.99.
Basic EV = 0.79 * 0.01 - 0.21 * 0.99 = -0.20 per dollar. Yet they show +$486k
realized PnL across 1,496 positions.

Something about our position-PnL computation or our interpretation is wrong.
This script investigates three hypotheses:

  H_A: piston777 exits losers early (sells before full 0→1 swing).
       Evidence: SELL trades at prices well above the entry, but the market
       hadn't fully resolved in their favor.
  H_B: Sizing asymmetry — they size far larger on the wins than the losses.
       Evidence: USDC size distribution is wildly different between winning
       and losing positions.
  H_C: Computation bug — BUY/SELL flip or outcome_index off-by-one, or
       position aggregation has a sign error.
       Evidence: PnL calculation doesn't reconcile to a naive per-trade check.

For each hypothesis, we produce a concrete metric.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/polymarket_strict_deep.db")
    parser.add_argument("--wallet-hint", default="piston777")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    # Resolve wallet
    row = conn.execute(
        "SELECT wallet, name FROM strict_wallets WHERE name LIKE ? OR wallet LIKE ?",
        (f"%{args.wallet_hint}%", f"%{args.wallet_hint}%"),
    ).fetchone()
    if not row:
        print(f"No wallet matching {args.wallet_hint}")
        return 1
    wallet, name = row
    print(f"Analyzing: {name} ({wallet})")
    print("=" * 90)

    # Pull all trades
    trades = conn.execute(
        """SELECT timestamp, condition_id, outcome_index, side, price, size,
                  usdc_size, title
           FROM strict_trades
           WHERE wallet = ?
           ORDER BY timestamp""",
        (wallet,),
    ).fetchall()
    trades = [dict(zip(
        ["ts", "cid", "oi", "side", "price", "size", "usdc", "title"], t
    )) for t in trades]
    n_trades = len(trades)
    print(f"Total trades: {n_trades:,}")

    # ============================================================
    # Hypothesis A — sell-side distribution
    # ============================================================
    print("\n" + "=" * 90)
    print("H_A: Do they exit losers early?")
    print("=" * 90)
    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]
    print(f"\n  BUY trades: {len(buys):,}  ({100*len(buys)/n_trades:.1f}%)")
    print(f"  SELL trades: {len(sells):,}  ({100*len(sells)/n_trades:.1f}%)")

    buy_prices = [t["price"] for t in buys if t["price"] is not None]
    sell_prices = [t["price"] for t in sells if t["price"] is not None]
    if buy_prices:
        print(f"\n  BUY price dist: p25={percentile(buy_prices,25):.3f} "
              f"p50={percentile(buy_prices,50):.3f} p75={percentile(buy_prices,75):.3f}")
    if sell_prices:
        print(f"  SELL price dist: p25={percentile(sell_prices,25):.3f} "
              f"p50={percentile(sell_prices,50):.3f} p75={percentile(sell_prices,75):.3f}")

    print(f"\n  INTERPRETATION: If SELL price distribution is similar to BUY,")
    print(f"  they're round-tripping at the same price level (arb / MM behavior)")
    print(f"  rather than holding to resolution.")

    # ============================================================
    # Hypothesis B — sizing asymmetry
    # ============================================================
    print("\n" + "=" * 90)
    print("H_B: Sizing asymmetry between winners and losers")
    print("=" * 90)

    # Aggregate positions
    positions = defaultdict(lambda: {
        "shares": 0.0, "usdc": 0.0, "n_trades": 0,
        "buy_usdc": 0.0, "sell_usdc": 0.0,
        "buy_shares": 0.0, "sell_shares": 0.0,
    })
    for t in trades:
        key = (t["cid"], t["oi"])
        p = positions[key]
        size = t["size"] or 0
        usdc = t["usdc"] or 0
        if t["side"] == "BUY":
            p["shares"] += size
            p["usdc"] -= usdc
            p["buy_usdc"] += usdc
            p["buy_shares"] += size
        else:
            p["shares"] -= size
            p["usdc"] += usdc
            p["sell_usdc"] += usdc
            p["sell_shares"] += size
        p["n_trades"] += 1

    # Get market settlements
    market_outcomes = {}
    for cid in set(k[0] for k in positions.keys()):
        row = conn.execute(
            "SELECT closed, outcome_prices FROM strict_markets WHERE condition_id = ?",
            (cid,),
        ).fetchone()
        if row:
            closed, op = row
            if closed and op:
                try:
                    market_outcomes[cid] = json.loads(op) if isinstance(op, str) else op
                except Exception:
                    pass

    # Compute per-position PnL both ways
    winners = []
    losers = []
    for (cid, oi), p in positions.items():
        payouts = market_outcomes.get(cid)
        if not payouts:
            continue
        try:
            payout = float(payouts[oi])
        except (IndexError, TypeError, ValueError):
            continue
        pnl = p["shares"] * payout + p["usdc"]

        # Alternative: what if we IGNORE sells and just compute naive BUY→payout
        # This tells us what happens if we interpret sells as "profit-taking" that
        # shouldn't go into PnL
        naive_pnl_buys_only = p["buy_shares"] * payout - p["buy_usdc"]

        pos_data = {
            "cid": cid, "oi": oi,
            "payout": payout,
            "buy_usdc": p["buy_usdc"], "sell_usdc": p["sell_usdc"],
            "buy_shares": p["buy_shares"], "sell_shares": p["sell_shares"],
            "net_shares": p["shares"], "net_usdc": p["usdc"],
            "pnl": pnl,
            "naive_pnl": naive_pnl_buys_only,
            "won": pnl > 1,
            "naive_won": naive_pnl_buys_only > 0,
        }
        if pnl > 0:
            winners.append(pos_data)
        elif pnl < 0:
            losers.append(pos_data)

    print(f"\n  Computed winning positions: {len(winners):,}")
    print(f"  Computed losing positions:  {len(losers):,}")
    total_pnl = sum(p["pnl"] for p in winners + losers)
    print(f"  Total realized PnL:         ${total_pnl:,.0f}")

    if winners and losers:
        win_usdc_p50 = percentile([p["buy_usdc"] for p in winners], 50)
        loss_usdc_p50 = percentile([p["buy_usdc"] for p in losers], 50)
        win_usdc_mean = sum(p["buy_usdc"] for p in winners) / len(winners)
        loss_usdc_mean = sum(p["buy_usdc"] for p in losers) / len(losers)
        print(f"\n  Buy USDC in WINNING positions:")
        print(f"    median: ${win_usdc_p50:,.0f}")
        print(f"    mean:   ${win_usdc_mean:,.0f}")
        print(f"    p90:    ${percentile([p['buy_usdc'] for p in winners], 90):,.0f}")
        print(f"    max:    ${max(p['buy_usdc'] for p in winners):,.0f}")
        print(f"\n  Buy USDC in LOSING positions:")
        print(f"    median: ${loss_usdc_p50:,.0f}")
        print(f"    mean:   ${loss_usdc_mean:,.0f}")
        print(f"    p90:    ${percentile([p['buy_usdc'] for p in losers], 90):,.0f}")
        print(f"    max:    ${max(p['buy_usdc'] for p in losers):,.0f}")
        print(f"\n  win / loss sizing ratio: {win_usdc_mean / max(loss_usdc_mean, 1):.2f}x")
        if win_usdc_mean > 2 * loss_usdc_mean:
            print(f"  → H_B CONFIRMED: winners are sized {win_usdc_mean/loss_usdc_mean:.1f}x larger")
        else:
            print(f"  → H_B NOT confirmed: sizing is roughly symmetric")

    # ============================================================
    # Hypothesis C — computation check
    # ============================================================
    print("\n" + "=" * 90)
    print("H_C: Does the computation reconcile to a simple per-trade check?")
    print("=" * 90)
    print(f"\n  Our computation: sum(net_shares * payout + net_usdc) for all resolved positions")
    print(f"  Alternative: sum(realized on each trade) — not applicable to position-based model")
    print(f"\n  Total computed PnL (position model): ${total_pnl:,.0f}")

    # Look at a specific winner in detail
    if winners:
        print(f"\n  Top 3 winning positions (in detail):")
        for p in sorted(winners, key=lambda x: -x["pnl"])[:3]:
            print(f"    cid={p['cid'][:12]}..., oi={p['oi']}, payout={p['payout']}")
            print(f"      buys: {p['buy_shares']:.1f} shares for ${p['buy_usdc']:,.0f}")
            print(f"      sells: {p['sell_shares']:.1f} shares for ${p['sell_usdc']:,.0f}")
            print(f"      net: {p['net_shares']:.1f} shares, ${p['net_usdc']:,.0f}")
            print(f"      realized: ${p['pnl']:,.0f}")
            avg_buy = p['buy_usdc'] / max(p['buy_shares'], 1)
            print(f"      avg buy price: {avg_buy:.4f}")
            if p['sell_shares'] > 0:
                avg_sell = p['sell_usdc'] / max(p['sell_shares'], 1)
                print(f"      avg sell price: {avg_sell:.4f}")

    # ============================================================
    # NEW hypothesis — maybe they're trading NO side primarily
    # ============================================================
    print("\n" + "=" * 90)
    print("H_D: Are they primarily trading outcome_index=1 (NO side)?")
    print("=" * 90)
    oi_counter = Counter(t["oi"] for t in trades)
    print(f"\n  outcome_index distribution:")
    for oi, n in sorted(oi_counter.items()):
        label = "YES (index 0)" if oi == 0 else "NO (index 1)"
        print(f"    {label}: {n:,} ({100*n/n_trades:.1f}%)")

    # Per-oi price stats
    for oi in [0, 1]:
        prices = [t["price"] for t in buys if t["oi"] == oi and t["price"] is not None]
        if prices:
            print(f"\n  BUY prices on oi={oi} ({'YES' if oi==0 else 'NO'}):")
            print(f"    p25={percentile(prices,25):.3f} p50={percentile(prices,50):.3f} "
                  f"p75={percentile(prices,75):.3f}")

    # Aha moment: if they buy NO at 0.99, it means they're betting AGAINST heavy favorites
    # And 79% of the time the heavy favorite DOES win (they lose)
    # But when they win, they get $1 on a $0.01 bet = 99:1 return
    # Let's check this:
    print("\n  Check: if oi=1 buys are at 0.99, are they buying NO-side deep favorites?")
    buys_oi1 = [t for t in buys if t["oi"] == 1]
    if buys_oi1:
        high_price_oi1 = [t for t in buys_oi1 if t["price"] and t["price"] >= 0.9]
        print(f"    oi=1 buys at price >=0.9: {len(high_price_oi1):,} ({100*len(high_price_oi1)/len(buys_oi1):.0f}%)")

    buys_oi0 = [t for t in buys if t["oi"] == 0]
    if buys_oi0:
        high_price_oi0 = [t for t in buys_oi0 if t["price"] and t["price"] >= 0.9]
        print(f"    oi=0 buys at price >=0.9: {len(high_price_oi0):,} ({100*len(high_price_oi0)/len(buys_oi0):.0f}%)")

    # Check win rate by outcome_index
    win_by_oi = {0: [0, 0], 1: [0, 0]}
    for p in winners + losers:
        won = p["pnl"] > 0
        win_by_oi[p["oi"]][0 if won else 1] += 1
    print(f"\n  Position WR by outcome_index:")
    for oi in [0, 1]:
        w, l = win_by_oi[oi]
        total = w + l
        wr = w / max(total, 1)
        print(f"    oi={oi} ({'YES' if oi==0 else 'NO'}): {w}W/{l}L = {wr:.3f} WR, {total} total")

    # ============================================================
    # Summary
    # ============================================================
    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    total_wr = len(winners) / max(len(winners) + len(losers), 1)
    print(f"\n  Position WR: {total_wr:.3f}")
    print(f"  Total PnL:   ${total_pnl:,.0f}")
    if winners and losers:
        avg_win = sum(p["pnl"] for p in winners) / len(winners)
        avg_loss = sum(p["pnl"] for p in losers) / len(losers)
        print(f"  Avg winning PnL: ${avg_win:,.0f}")
        print(f"  Avg losing PnL:  ${avg_loss:,.0f}")
        print(f"  Win/loss PnL ratio: {abs(avg_win / avg_loss):.2f}")
        # EV check
        ev = total_wr * avg_win + (1 - total_wr) * avg_loss
        print(f"  Expected value per position: ${ev:,.0f}")
        if ev > 0:
            print(f"  → EV is positive, the math reconciles")
        else:
            print(f"  → EV is negative, the total PnL must come from something else")


if __name__ == "__main__":
    main()
