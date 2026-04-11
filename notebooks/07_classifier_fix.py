#!/usr/bin/env python3
"""
Track 2A — Fix category classifier using event slug prefixes.

Initial attempt used Gamma API `category` field: fails because Gamma returns
null for that field on our markets. The `events` response (via /events?slug=)
has `tags: ['Sports','NBA','Games','Basketball']` but at 44k unique event
slugs that's a 5-10 min API pull.

Better approach: the event slugs themselves are machine-generated with clean
prefixes (nba-lal-det-..., epl-liv-man-..., btc-up-or-down-..., atp-...-2026).
~302 unique first-tokens cover 44,867 event slugs. Rule-based mapping covers
95%+ with zero API calls.

This script:
1. Loads all strict trades with event_slug
2. Classifies each trade via slug-prefix rules
3. Writes back to strict_markets.category_v2 (market-level, derived from
   the modal category across all trades in that market)
4. Re-applies strict-filter semantics with HFT exclusion and produces a
   corrected per-trader report.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone


# --------------- slug-prefix taxonomy --------------- #
#
# First-token or leading-pattern of event_slug maps to a canonical category.
# Order matters: more specific prefixes first.

SLUG_PREFIX_RULES = [
    # Crypto (biggest group)
    (r"^btc[-_]",                "crypto_btc"),
    (r"^bitcoin[-_]",            "crypto_btc"),
    (r"^eth[-_]",                "crypto_eth"),
    (r"^ethereum[-_]",           "crypto_eth"),
    (r"^xrp[-_]",                "crypto_xrp"),
    (r"^sol[-_]",                "crypto_sol"),
    (r"^solana[-_]",             "crypto_sol"),
    (r"^bnb[-_]",                "crypto_bnb"),
    (r"^doge[-_]",               "crypto_doge"),
    (r"^dogecoin[-_]",           "crypto_doge"),
    (r"^hype[-_]",               "crypto_other"),
    (r"^ada[-_]",                "crypto_other"),
    (r"^avax[-_]",               "crypto_other"),
    (r"^matic[-_]",              "crypto_other"),
    (r"^link[-_]",               "crypto_other"),
    (r"^dot[-_]",                "crypto_other"),
    (r"^ltc[-_]",                "crypto_other"),
    (r"^trx[-_]",                "crypto_other"),
    (r"^near[-_]",               "crypto_other"),
    (r"^atom[-_]",               "crypto_other"),
    (r"^tia[-_]",                "crypto_other"),
    (r"^sui[-_]",                "crypto_other"),
    (r"^inj[-_]",                "crypto_other"),
    (r"^sei[-_]",                "crypto_other"),
    (r"^apt[-_]",                "crypto_other"),
    (r"^op[-_]",                 "crypto_other"),
    (r"^arb[-_]",                "crypto_other"),
    (r"^pepe[-_]",               "crypto_other"),
    (r"^shib[-_]",               "crypto_other"),
    (r"^wif[-_]",                "crypto_other"),
    (r"^bonk[-_]",                "crypto_other"),
    (r"^what-price-will-bitcoin", "crypto_btc"),
    (r"^what-price-will-ethereum","crypto_eth"),
    (r"^what-price-will-solana",  "crypto_sol"),
    (r"^what-price-will-xrp",     "crypto_xrp"),
    (r"^what-price-will-[a-z]+",  "crypto_other"),
    # NBA
    (r"^nba[-_]",                "sports_nba"),
    (r"^(lal|bos|gsw|mia|nyk|den|dal|phi|mil|phx|lac|mem|chi|nop|sac|hou|min|okc|orl|cha|was|atl|det|cle|brk|por|uta|sas|tor|ind)[-_].*\d{4}",
                                 "sports_nba"),  # NBA team code prefixes
    # NFL
    (r"^nfl[-_]",                "sports_nfl"),
    (r"^cfb[-_]",                "sports_ncaa_football"),
    # MLB
    (r"^mlb[-_]",                "sports_mlb"),
    # NHL
    (r"^nhl[-_]",                "sports_nhl"),
    # College
    (r"^cbb[-_]",                "sports_ncaa_basketball"),
    (r"^cfb[-_]",                "sports_ncaa_football"),
    (r"^ncaa[-_]",               "sports_ncaa"),
    # Soccer — European leagues
    (r"^epl[-_]",                "sports_soccer_epl"),
    (r"^efl[-_]",                "sports_soccer_efl"),
    (r"^elc[-_]",                "sports_soccer_efl_championship"),
    (r"^ucl[-_]",                "sports_soccer_ucl"),
    (r"^uel[-_]",                "sports_soccer_uel"),
    (r"^bun[-_]",                "sports_soccer_bundesliga"),
    (r"^bl2[-_]",                "sports_soccer_bundesliga2"),
    (r"^fl1[-_]",                "sports_soccer_ligue1"),
    (r"^fl2[-_]",                "sports_soccer_ligue2"),
    (r"^ita[-_]",                "sports_soccer_seriea"),
    (r"^liga[-_]",               "sports_soccer_laliga"),
    (r"^spa[-_]",                "sports_soccer_laliga"),
    (r"^lpo[-_]",                "sports_soccer_portugal"),
    (r"^ned[-_]",                "sports_soccer_eredivisie"),
    (r"^por[-_]",                "sports_soccer_portugal"),
    # Soccer — Americas
    (r"^mls[-_]",                "sports_soccer_mls"),
    (r"^bra[-_]",                "sports_soccer_brazil"),
    (r"^arg[-_]",                "sports_soccer_argentina"),
    (r"^mex[-_]",                "sports_soccer_mexico"),
    (r"^chi[-_]",                "sports_soccer_chile"),  # risk of NBA chi conflict handled earlier
    # Soccer — Asia / Oceania
    (r"^j\d",                    "sports_soccer_jleague"),
    (r"^k\d",                    "sports_soccer_kleague"),
    (r"^a-league",               "sports_soccer_aleague"),
    (r"^jup",                    "sports_soccer_belgium"),
    # Soccer — other Europe
    (r"^tur[-_]",                "sports_soccer_turkey"),
    (r"^rus[-_]",                "sports_soccer_russia"),
    (r"^gre[-_]",                "sports_soccer_greece"),
    (r"^cro[-_]",                "sports_soccer_croatia"),
    (r"^cze[-_]",                "sports_soccer_czech"),
    (r"^pol[-_]",                "sports_soccer_poland"),
    (r"^ukr[-_]",                "sports_soccer_ukraine"),
    (r"^aut[-_]",                "sports_soccer_austria"),
    (r"^swi[-_]",                "sports_soccer_switzerland"),
    (r"^bel[-_]",                "sports_soccer_belgium"),
    (r"^nor[-_]",                "sports_soccer_norway"),
    (r"^swe[-_]",                "sports_soccer_sweden"),
    (r"^den[-_]",                "sports_soccer_denmark"),
    (r"^fin[-_]",                "sports_soccer_finland"),
    (r"^scp[-_]",                "sports_soccer_scotland"),
    (r"^sco[-_]",                "sports_soccer_scotland"),
    (r"^spl[-_]",                "sports_soccer_scotland"),
    # International soccer
    (r"^wc[-_]",                 "sports_soccer_worldcup"),
    (r"^eur[-_]",                "sports_soccer_euros"),
    (r"^nations-league",         "sports_soccer_nationsleague"),
    (r"^copa",                   "sports_soccer_copa"),
    # Tennis
    (r"^atp[-_]",                "sports_tennis_atp"),
    (r"^wta[-_]",                "sports_tennis_wta"),
    (r"^.*?(australian-open|french-open|wimbledon|us-open)-.*(winner|tennis)",
                                 "sports_tennis_grandslam"),
    # MMA / UFC / Boxing
    (r"^ufc[-_]",                "sports_mma_ufc"),
    (r"^pfl[-_]",                "sports_mma_pfl"),
    (r"^boxing[-_]",             "sports_boxing"),
    # F1 / Motorsport
    (r"^f1[-_]",                 "sports_f1"),
    (r"^.*-grand-prix",          "sports_f1"),
    (r"^motogp",                 "sports_motogp"),
    (r"^indycar",                "sports_indycar"),
    (r"^nascar",                 "sports_nascar"),
    # Golf
    (r"^pga[-_]",                "sports_golf"),
    (r"^liv[-_]",                "sports_golf"),
    (r"^masters[-_]",            "sports_golf"),
    (r"^the-masters",            "sports_golf"),
    # Cricket
    (r"^cricket",                "sports_cricket"),
    (r"^ipl[-_]",                "sports_cricket"),
    (r"^test-match",             "sports_cricket"),
    # Rugby
    (r"^rugby",                  "sports_rugby"),
    (r"^nrl[-_]",                "sports_rugby"),
    # Olympics
    (r"^olympic",                "sports_olympics"),
    (r"^summer-olympic",         "sports_olympics"),
    (r"^winter-olympic",         "sports_olympics"),
    # Esports
    (r"^lol[-_]",                "esports_lol"),
    (r"^cs2[-_]",                "esports_cs2"),
    (r"^csgo[-_]",               "esports_csgo"),
    (r"^dota2[-_]",              "esports_dota2"),
    (r"^dota[-_]",               "esports_dota2"),
    (r"^val[-_]",                "esports_valorant"),
    (r"^es2[-_]",                "esports_other"),
    (r"^rl[-_]",                 "esports_rocket_league"),
    (r"^ow[-_]",                 "esports_overwatch"),
    (r"^sc2[-_]",                "esports_sc2"),
    # Poker / gambling / entertainment
    (r"^poker",                  "games_poker"),
    (r"^.*poker",                "games_poker"),
    (r"^.*chess",                "games_chess"),
    # News / events (the "will-X" prefix)
    (r"^will-",                  "news_will"),
    (r"^when-",                  "news_when"),
    # Politics
    (r"^trump[-_]",              "politics_us"),
    (r"^biden[-_]",              "politics_us"),
    (r"^harris[-_]",              "politics_us"),
    (r"^2024-",                  "politics_2024"),
    (r"^2025-",                  "misc_2025"),
    (r"^2026-",                  "misc_2026"),
    (r"^presidential",           "politics_us"),
    (r"^senate",                 "politics_us"),
    (r"^house",                  "politics_us"),
    (r"^election",               "politics_us"),
]

# Soccer codes that risk colliding with NBA/NFL team codes — handle last.
SOCCER_COUNTRY_CODE_RE = re.compile(r"^(ita|spa|por|eng|ger|fra|sco|bel|ned|nor|swe|den|fin|rus|ukr|tur|gre|cro|cze|pol|aut|swi|isr)[-_]")


def classify_slug(slug: str) -> str:
    """Apply slug-prefix rules. Returns canonical category or 'misc'."""
    if not slug:
        return "misc"
    s = slug.lower()
    for pattern, canon in SLUG_PREFIX_RULES:
        if re.match(pattern, s):
            return canon
    # Second chance: soccer country code
    m = SOCCER_COUNTRY_CODE_RE.match(s)
    if m:
        return f"sports_soccer_{m.group(1)}"
    return "misc"


def broad_category(cat: str) -> str:
    """Collapse fine-grained categories into a broad bucket for filter semantics."""
    if cat.startswith("sports_"):
        return "sports"
    if cat.startswith("crypto"):
        return "crypto"
    if cat.startswith("esports"):
        return "esports"
    if cat.startswith("politics"):
        return "politics"
    if cat.startswith("news"):
        return "news"
    if cat.startswith("games"):
        return "games"
    if cat == "weather":
        return "weather"
    return "misc"


# --------------- main --------------- #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/polymarket_strict_deep.db")
    parser.add_argument("--out", default="data/phase2a_reclassification_report.json")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    # Ensure columns exist
    cols = [r[1] for r in conn.execute("PRAGMA table_info(strict_markets)").fetchall()]
    if "category_v2" not in cols:
        conn.execute("ALTER TABLE strict_markets ADD COLUMN category_v2 TEXT")
    if "category_broad" not in cols:
        conn.execute("ALTER TABLE strict_markets ADD COLUMN category_broad TEXT")
    conn.commit()

    # Stage 1: classify at the trade level, then derive per-market category as modal.
    # Read from BOTH strict_trades and honorable_trades so we classify all markets,
    # not just those traded by strict-tier wallets.
    print("[Stage 1] classifying trades by event_slug prefix")
    # Check if honorable_trades table exists
    hon_exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='honorable_trades'"
    ).fetchone()[0]
    if hon_exists:
        trades = conn.execute(
            """SELECT condition_id, event_slug FROM strict_trades
               UNION ALL
               SELECT condition_id, event_slug FROM honorable_trades"""
        ).fetchall()
    else:
        trades = conn.execute(
            "SELECT condition_id, event_slug FROM strict_trades"
        ).fetchall()
    print(f"  {len(trades):,} trades to classify")

    market_cat_votes: dict = defaultdict(Counter)
    for cid, event_slug in trades:
        cat = classify_slug(event_slug or "")
        market_cat_votes[cid][cat] += 1

    # Each market → modal classification
    market_to_cat = {}
    for cid, votes in market_cat_votes.items():
        top_cat, _ = votes.most_common(1)[0]
        market_to_cat[cid] = top_cat

    print(f"  {len(market_to_cat):,} unique markets classified")

    # Stage 2: write back to strict_markets
    print("[Stage 2] writing category_v2 to strict_markets")
    rows = [(cat, broad_category(cat), cid) for cid, cat in market_to_cat.items()]
    conn.executemany(
        "UPDATE strict_markets SET category_v2 = ?, category_broad = ? WHERE condition_id = ?",
        rows,
    )
    conn.commit()

    # Stage 3: market-level category distribution
    print("\n[Stage 3] market-level v2 category distribution")
    v2_counter = Counter(r[0] or "(null)" for r in conn.execute(
        "SELECT category_v2 FROM strict_markets"
    ).fetchall())
    broad_counter = Counter(r[0] or "(null)" for r in conn.execute(
        "SELECT category_broad FROM strict_markets"
    ).fetchall())

    total_markets = sum(v2_counter.values())
    print(f"\n  Total markets: {total_markets:,}")

    print(f"\n  BROAD category distribution (markets):")
    for cat, n in broad_counter.most_common():
        print(f"    {cat:<20} {n:>8,}  ({100*n/total_markets:>5.1f}%)")

    print(f"\n  FINE category v2 — top 40 markets:")
    for cat, n in v2_counter.most_common(40):
        print(f"    {cat:<35} {n:>8,}  ({100*n/total_markets:>5.1f}%)")

    # Stage 4: per-trader recomputation
    print("\n[Stage 4] per-trader broad-category breakdown")
    print("=" * 110)

    wallets = conn.execute(
        """SELECT wallet, name, step2_total_trades, step2_realized_pnl,
                  step2_win_rate, step2_fast_settling
           FROM strict_wallets ORDER BY step2_realized_pnl DESC"""
    ).fetchall()

    # Per-trade joins
    trade_rows = conn.execute("""
        SELECT t.wallet, COALESCE(m.category_broad,'misc'), COALESCE(m.category_v2,'misc')
        FROM strict_trades t
        LEFT JOIN strict_markets m ON t.condition_id = m.condition_id
    """).fetchall()

    per_w_broad = defaultdict(Counter)
    per_w_fine = defaultdict(Counter)
    per_w_total = Counter()
    for w, bc, fc in trade_rows:
        per_w_broad[w][bc] += 1
        per_w_fine[w][fc] += 1
        per_w_total[w] += 1

    per_w_tpd = {}
    for w in per_w_total:
        r = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM strict_trades WHERE wallet = ?",
            (w,),
        ).fetchone()
        if r and r[0] and r[1] and r[1] > r[0]:
            days = (r[1] - r[0]) / 86400
            per_w_tpd[w] = r[2] / max(days, 1)
        else:
            per_w_tpd[w] = 0

    print(f"\n  {'name':<26} {'total':>7}  {'broad breakdown':<62}  {'t/day':>7}  verdict")
    print("  " + "-" * 130)

    wallet_reports = []
    still_sports = []
    reclass_crypto = []
    reclass_hft = []
    reclass_other = []

    for wallet, name, s2_tt, s2_pnl, s2_wr, s2_fs in wallets:
        broad = per_w_broad[wallet]
        fine = per_w_fine[wallet]
        total = per_w_total[wallet]
        if not total:
            continue
        sports_pct = 100 * broad.get("sports", 0) / total
        crypto_pct = 100 * broad.get("crypto", 0) / total
        news_pct = 100 * broad.get("news", 0) / total
        games_pct = 100 * broad.get("games", 0) / total
        esports_pct = 100 * broad.get("esports", 0) / total
        politics_pct = 100 * broad.get("politics", 0) / total
        misc_pct = 100 * broad.get("misc", 0) / total
        tpd = per_w_tpd[wallet]

        broad_str = (
            f"sp={sports_pct:>4.0f}% "
            f"cr={crypto_pct:>4.0f}% "
            f"es={esports_pct:>4.0f}% "
            f"nw={news_pct:>4.0f}% "
            f"pl={politics_pct:>4.0f}% "
            f"mi={misc_pct:>4.0f}%"
        )

        # Verdict
        if tpd >= 500:
            verdict = "HFT_bot_drop"
            reclass_hft.append(name)
        elif crypto_pct >= 50:
            verdict = "crypto_specialist_drop"
            reclass_crypto.append(name)
        elif sports_pct >= 40:
            verdict = "sports_specialist_keep"
            still_sports.append((name, sports_pct, fine))
        elif sports_pct >= 20:
            verdict = "multi_cat_sports_heavy"
            still_sports.append((name, sports_pct, fine))
        else:
            verdict = "other_specialist"
            reclass_other.append(name)

        name_short = (name or wallet[:12])[:26]
        print(f"  {name_short:<26} {total:>7,}  {broad_str:<62}  {tpd:>7.1f}  {verdict}")

        wallet_reports.append({
            "wallet": wallet,
            "name": name,
            "total_trades": total,
            "broad_breakdown": dict(broad),
            "top_fine_categories": dict(fine.most_common(10)),
            "trades_per_day": round(tpd, 1),
            "sports_pct": round(sports_pct, 1),
            "crypto_pct": round(crypto_pct, 1),
            "esports_pct": round(esports_pct, 1),
            "verdict": verdict,
            "step2_total_trades": s2_tt,
            "step2_realized_pnl": s2_pnl,
            "step2_win_rate": s2_wr,
            "step2_fast_settling": s2_fs,
        })

    # Stage 5: top fine sub-categories for remaining sports traders
    print("\n" + "=" * 110)
    print("TOP FINE SUB-CATEGORIES PER SURVIVING SPORTS TRADER")
    print("=" * 110)
    for name, sp, fine in still_sports:
        top3 = [f"{c}={n:,}" for c, n in fine.most_common(5) if not c.startswith("misc")]
        print(f"  {(name or '?')[:28]:<28} sports={sp:>4.0f}%  top: {', '.join(top3)}")

    # Stage 6: summary
    print("\n" + "=" * 110)
    print("RECLASSIFICATION SUMMARY")
    print("=" * 110)
    print(f"  HFT bots to drop from strict:            {len(reclass_hft)}")
    for n in reclass_hft:
        print(f"    - {n}")
    print(f"  Crypto specialists to drop from sports: {len(reclass_crypto)}")
    for n in reclass_crypto:
        print(f"    - {n}")
    print(f"  Other-category specialists:             {len(reclass_other)}")
    for n in reclass_other:
        print(f"    - {n}")
    print(f"  Remain in sports pool:                  {len(still_sports)}")
    for name, sp, _ in still_sports:
        print(f"    - {(name or '?'):<26} ({sp:.0f}% sports)")

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "db": args.db,
        "total_markets": total_markets,
        "broad_distribution_markets": dict(broad_counter),
        "fine_distribution_markets_top50": dict(v2_counter.most_common(50)),
        "per_wallet_reports": wallet_reports,
        "summary": {
            "hft_bot_drop": reclass_hft,
            "crypto_specialist_drop": reclass_crypto,
            "other_specialist": reclass_other,
            "sports_pool": [n for n, _, _ in still_sports],
        },
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  report → {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
