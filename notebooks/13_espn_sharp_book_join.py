#!/usr/bin/env python3
"""
Phase 3d/e — Join 0x006cc834Cc's Euro soccer moneyline trades to ESPN
match results and embedded DraftKings odds.

For each "Will [team] win on YYYY-MM-DD?" moneyline position they took NO on:
  1. Parse the team name and date from the market title.
  2. Look up the match on ESPN for the appropriate league
     (uefa.champions / eng.1 / fra.1 / etc.)
  3. Record the actual result (did the favorite win?)
  4. Record ESPN's DraftKings embedded moneyline odds if present
  5. Compare Polymarket's implied probability vs DraftKings' implied
     probability

The core question: is Polymarket systematically pricing favorites higher
than sharp books? If yes, the fade-favorite rule is a mispricing edge.
If no, it may be stock-market risk premium or something else.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# Map our category_v2 to ESPN league slug
LEAGUE_MAP = {
    "sports_soccer_ucl": "uefa.champions",
    "sports_soccer_epl": "eng.1",
    "sports_soccer_laliga": "esp.1",
    "sports_soccer_ligue1": "fra.1",
    "sports_soccer_bundesliga": "ger.1",
    "sports_soccer_seriea": "ita.1",
}

# Also try second-tier and UEL for missing matches
FALLBACK_LEAGUES = {
    "sports_soccer_ucl": ["uefa.europa", "uefa.europa.conf"],
    "sports_soccer_epl": ["eng.2", "eng.fa"],
}


def parse_market_title(title: str) -> tuple[str | None, str | None]:
    """
    Parse "Will [Team Name] win on YYYY-MM-DD?" → (team_name, date_str).
    """
    if not title:
        return None, None
    m = re.match(r"Will\s+(.*?)\s+win\s+on\s+(\d{4}-\d{2}-\d{2})\??", title)
    if not m:
        return None, None
    return m.group(1).strip(), m.group(2)


def fetch_espn_scoreboard(league: str, date_str: str, cache: dict) -> list:
    """Fetch ESPN events for a league on a given date (YYYY-MM-DD)."""
    cache_key = (league, date_str)
    if cache_key in cache:
        return cache[cache_key]
    ymd = date_str.replace("-", "")
    url = f"{ESPN_BASE}/{league}/scoreboard"
    try:
        r = requests.get(url, params={"dates": ymd}, timeout=15)
        if r.status_code != 200:
            cache[cache_key] = []
            return []
        events = r.json().get("events", []) or []
        cache[cache_key] = events
        return events
    except Exception:
        cache[cache_key] = []
        return []


def extract_event_info(event: dict) -> dict:
    """Extract match info: teams, score, winner, DraftKings odds."""
    comp = (event.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    teams = []
    for c in competitors:
        team_obj = c.get("team") or {}
        teams.append({
            "name": team_obj.get("displayName") or team_obj.get("name") or "",
            "home_away": c.get("homeAway"),
            "score": c.get("score"),
            "winner": bool(c.get("winner")),
        })
    status = ((comp.get("status") or {}).get("type") or {}).get("description", "")

    # Find a winner
    winning_team = None
    for t in teams:
        if t["winner"]:
            winning_team = t["name"]
            break

    # Odds (if present)
    odds = comp.get("odds") or []
    provider_odds = []
    for o in odds:
        if not o:
            continue
        provider = ((o.get("provider") or {}).get("name")) or "unknown"
        home_team_odds = o.get("homeTeamOdds") or {}
        away_team_odds = o.get("awayTeamOdds") or {}
        provider_odds.append({
            "provider": provider,
            "home_ml": home_team_odds.get("moneyLine"),
            "away_ml": away_team_odds.get("moneyLine"),
            "home_favorite": home_team_odds.get("favorite"),
            "away_favorite": away_team_odds.get("favorite"),
            "spread": o.get("spread"),
            "over_under": o.get("overUnder"),
        })

    return {
        "espn_id": event.get("id"),
        "date": event.get("date"),
        "status": status,
        "teams": teams,
        "winning_team": winning_team,
        "odds": provider_odds,
    }


def fuzzy_match(query: str, candidates: list) -> str | None:
    """Find the best candidate name matching `query`. Case-insensitive substring or vice-versa."""
    if not query or not candidates:
        return None
    q = query.lower()
    # Exact or substring match
    for c in candidates:
        if c.lower() == q or q in c.lower() or c.lower() in q:
            return c
    # Token overlap (handles "FC Bayern München" vs "Bayern Munich")
    q_tokens = set(re.findall(r"[a-z]+", q))
    q_tokens -= {"fc", "sc", "cf", "the", "sk", "sc", "afc", "cd"}
    best, best_score = None, 0
    for c in candidates:
        c_tokens = set(re.findall(r"[a-z]+", c.lower()))
        overlap = len(q_tokens & c_tokens)
        if overlap > best_score:
            best, best_score = c, overlap
    return best if best_score >= 1 else None


def moneyline_to_probability(ml: int | None) -> float | None:
    """Convert American moneyline to implied probability."""
    if ml is None:
        return None
    try:
        ml = int(ml)
    except (ValueError, TypeError):
        return None
    if ml > 0:
        return 100 / (ml + 100)
    elif ml < 0:
        return -ml / (-ml + 100)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/polymarket_strict_deep.db")
    parser.add_argument("--wallet", default="0x006cc834cc092684f1b56626e23bedb3835c16ea")
    parser.add_argument("--out", default="data/phase3d_espn_join.json")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    # Load all 0x006cc834Cc's Euro soccer moneyline NO positions
    rows = conn.execute("""
        SELECT t.condition_id, t.outcome_index, t.title, m.category_v2,
               m.outcome_prices, m.closed, m.resolve_ts,
               SUM(CASE WHEN t.side='BUY' THEN t.usdc_size ELSE 0 END) AS buy_usdc,
               SUM(CASE WHEN t.side='BUY' THEN t.size ELSE 0 END) AS buy_shares,
               SUM(CASE WHEN t.side='SELL' THEN t.size ELSE 0 END) AS sell_shares,
               SUM(CASE WHEN t.side='BUY' THEN t.size ELSE -t.size END) AS net_shares,
               SUM(CASE WHEN t.side='BUY' THEN -t.usdc_size ELSE t.usdc_size END) AS net_usdc
        FROM strict_trades t
        LEFT JOIN strict_markets m ON t.condition_id = m.condition_id
        WHERE t.wallet = ?
          AND m.category_v2 IN ('sports_soccer_ucl','sports_soccer_epl',
                                 'sports_soccer_ligue1','sports_soccer_bundesliga',
                                 'sports_soccer_laliga','sports_soccer_seriea')
          AND m.closed = 1
          AND t.title LIKE 'Will % win on %'
        GROUP BY t.condition_id, t.outcome_index
        ORDER BY t.condition_id, t.outcome_index
    """, (args.wallet,)).fetchall()

    print(f"Found {len(rows)} Euro soccer moneyline positions")

    cache = {}
    results = []

    for (cid, oi, title, cat, op, closed, resolve_ts,
         buy_usdc, buy_shares, sell_shares, net_shares, net_usdc) in rows:

        team, date_str = parse_market_title(title)
        if not team or not date_str:
            continue

        # Compute position PnL
        try:
            prices = json.loads(op) if isinstance(op, str) else op
            payout = float(prices[oi])
        except Exception:
            payout = None

        if payout is not None:
            pnl = (net_shares or 0) * payout + (net_usdc or 0)
        else:
            pnl = None

        side_label = "YES" if oi == 0 else "NO"

        # Lookup ESPN match
        league = LEAGUE_MAP.get(cat)
        if not league:
            continue
        events = fetch_espn_scoreboard(league, date_str, cache)

        # Try fallbacks if not found
        if not events and cat in FALLBACK_LEAGUES:
            for fb in FALLBACK_LEAGUES[cat]:
                events = fetch_espn_scoreboard(fb, date_str, cache)
                if events:
                    break

        # Match the team to a specific event
        matched_event = None
        for e in events:
            info = extract_event_info(e)
            team_names = [t["name"] for t in info["teams"]]
            matched = fuzzy_match(team, team_names)
            if matched:
                matched_event = info
                matched_event["query_team"] = team
                matched_event["matched_team"] = matched
                matched_event["team_won"] = (info["winning_team"] == matched) if info["winning_team"] else None
                break

        # Record result
        record = {
            "cid": cid[:12] + "...",
            "title": title,
            "category": cat,
            "side": side_label,
            "team_query": team,
            "date": date_str,
            "polymarket_pnl": pnl,
            "buy_usdc": buy_usdc,
            "buy_shares_yes_or_no": buy_shares,
            "matched_event": matched_event,
        }
        results.append(record)

        # Progress
        if matched_event:
            result_str = f"team_won={matched_event['team_won']}"
            pm_won = pnl and pnl > 0
            # For NO bets: pm_won iff team LOST. Consistency check.
            consistency = ""
            if side_label == "NO" and pm_won and matched_event["team_won"] is False:
                consistency = " ✓ consistent"
            elif side_label == "NO" and pm_won is False and matched_event["team_won"] is True:
                consistency = " ✓ consistent"
            else:
                consistency = " ?"

            # ESPN odds (if present)
            espn_odds = matched_event.get("odds", [])
            odds_str = ""
            if espn_odds:
                o = espn_odds[0]
                odds_str = f"  dk_spread={o.get('spread','-')}  dk_ou={o.get('over_under','-')}"

            print(f"  {date_str}  {team[:20]:<20}  side={side_label}  "
                  f"pnl=${(pnl or 0):>11,.0f}  {result_str}{consistency}{odds_str}")
        else:
            print(f"  {date_str}  {team[:20]:<20}  side={side_label}  "
                  f"pnl=${(pnl or 0):>11,.0f}  NO MATCH on ESPN")

        time.sleep(0.3)  # gentle pacing

    # Aggregate
    matched_count = sum(1 for r in results if r["matched_event"])
    total_count = len(results)
    print(f"\n{'=' * 80}")
    print(f"SUMMARY: {matched_count}/{total_count} positions matched to ESPN events")
    print(f"{'=' * 80}")

    if matched_count > 0:
        # Check consistency: for NO bets, positive PnL should align with team losing
        consistent = 0
        inconsistent = 0
        for r in results:
            if not r["matched_event"]:
                continue
            team_won = r["matched_event"]["team_won"]
            if team_won is None:
                continue
            pnl = r["polymarket_pnl"] or 0
            side = r["side"]
            if side == "NO":
                expected_positive = not team_won
            else:
                expected_positive = team_won
            actual_positive = pnl > 0
            if expected_positive == actual_positive:
                consistent += 1
            else:
                inconsistent += 1

        print(f"  Pnl-vs-outcome consistency: {consistent} consistent, {inconsistent} inconsistent")

    # Save report
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "wallet": args.wallet,
            "n_positions": total_count,
            "n_matched": matched_count,
            "results": results,
        }, f, indent=2, default=str)
    print(f"\n  report → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
