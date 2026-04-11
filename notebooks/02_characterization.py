#!/usr/bin/env python3
"""
Step 2 — Polymarket trader universe characterization.

Seeds from 4 leaderboards (profit/volume × 30d/7d windows), deduplicates,
runs a tiered pull:
  - Tier A: most recent 500 trades per trader (cheap headline pass)
  - Tier B: full timestamp-paginated activity for traders that cleared Tier A
  - Metadata cache: batched Gamma lookups (closed=true + closed=false merge)

Computes per-trader stats: fast-settling trade count, category breakdown,
realized PnL from position aggregation, win rate, block-less bootstrap p-value
on position-level PnL, and both strict and honorable filter membership.

Fixes applied from Step 1 findings:
  F1: timestamp pagination (end=<earliest-1>) instead of offset
  F2: Gamma batch with closed=true + closed=false both required
  F3: fast-settling is per-trade (resolve_ts - trade_ts ≤ 72h), not per-market
  F4/F5: seeds from 7d/30d windows only (not 'all')

Output:
  data/polymarket_traders.db    SQLite with trader_stats + run_meta
  data/polymarket_step2_summary.json  Full JSON dump for notebook analysis
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

# ------------------------------ config ------------------------------ #

LB_API = "https://lb-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

DEFAULT_RATE = 5.0
GAMMA_BATCH_SIZE = 50
FAST_SETTLING_SECONDS = 72 * 3600
MAX_TIER_B_PAGES = 300
BOOTSTRAP_ITERS = 2000

log = logging.getLogger("step2")


# ------------------------------ http client ------------------------------ #

class RateLimiter:
    def __init__(self, rate: float):
        self.min_interval = 1.0 / rate
        self.last = 0.0

    def wait(self):
        now = time.monotonic()
        delay = self.last + self.min_interval - now
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()

    def set_rate(self, rate: float):
        self.min_interval = 1.0 / rate


class PolyClient:
    def __init__(self, rate: float = DEFAULT_RATE, timeout: int = 20, max_retries: int = 4):
        self.rl = RateLimiter(rate)
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.stats: dict = defaultdict(int)

    def _get(self, url: str, params=None):
        self.rl.wait()
        self.stats["requests"] += 1
        for attempt in range(self.max_retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429:
                    self.stats["429s"] += 1
                    time.sleep(2 ** (attempt + 1))
                    continue
                if r.status_code == 404:
                    return None
                if r.status_code >= 500:
                    self.stats["5xx"] += 1
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                return r.json()
            except (requests.exceptions.RequestException, ValueError):
                self.stats["errors"] += 1
                if attempt == self.max_retries - 1:
                    return None
                time.sleep(2 ** attempt)
        return None

    def leaderboard(self, kind: str, window: str, limit: int) -> list:
        r = self._get(f"{LB_API}/{kind}", params={"window": window, "limit": limit})
        return r if isinstance(r, list) else []

    def activity_page(self, wallet: str, limit: int = 500, end: int | None = None) -> list:
        params = {"user": wallet, "type": "TRADE", "limit": limit}
        if end is not None:
            params["end"] = end
        r = self._get(f"{DATA_API}/activity", params=params)
        return r if isinstance(r, list) else []

    def all_activity(self, wallet: str, max_pages: int = MAX_TIER_B_PAGES) -> list:
        out: list = []
        end = None
        last_earliest = None
        for _ in range(max_pages):
            batch = self.activity_page(wallet, limit=500, end=end)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 500:
                break
            earliest = min((t.get("timestamp", 0) or 0) for t in batch)
            if earliest == 0 or earliest == last_earliest:
                break
            last_earliest = earliest
            end = earliest - 1
        return out

    def markets_by_cids(self, cids: list) -> dict:
        out: dict = {}
        unique = list(set(cids))
        for i in range(0, len(unique), GAMMA_BATCH_SIZE):
            batch = unique[i:i + GAMMA_BATCH_SIZE]
            for closed in ("true", "false"):
                params = [("condition_ids", c) for c in batch]
                params += [("closed", closed), ("limit", "100")]
                r = self._get(f"{GAMMA_API}/markets", params=params)
                if not isinstance(r, list):
                    continue
                for m in r:
                    cid = m.get("conditionId")
                    if cid and cid in batch:
                        out[cid] = m
        return out


# ------------------------------ classification ------------------------------ #

_SPORTS_TOKENS = (
    " vs.", " vs ", "-vs-",
    " fc ", " fc,", "united", " city", "rovers", "athletic",
    "nba ", "nfl ", "mlb ", "nhl ", "mls ",
    "uefa", "premier league", "la liga", "serie a", "bundesliga", "ligue 1",
    "j-league", "j1 ", "j2 ", "k league",
    "wimbledon", "roland garros", "us open", "australian open",
    "atp ", "wta ", "ufc ", " mma", "formula 1", " f1 ", " f1,",
    "grand prix", "moto gp", "cricket", "rugby",
    "o/u ", "over/under", "moneyline", "-1.5", "+1.5", "-2.5", "+2.5",
)

_WEATHER_TOKENS = (
    "temperature", " weather", "rainfall", "high temp",
    "low temp", "°f", "°c", "snowfall",
)

_CRYPTO_COINS = ("bitcoin", "btc", "ethereum", "eth", "solana", "sol ")
_CRYPTO_PRICE_HINTS = ("above $", "below $", "between $", "hit $", "reach $")


def classify_category(title: str, slug: str = "", event_slug: str = "") -> str:
    t = (title or "").lower()
    s = (slug or "").lower()
    e = (event_slug or "").lower()
    blob = " " + t + " " + s + " " + e + " "

    if any(tok in blob for tok in _SPORTS_TOKENS):
        return "sports"
    if any(tok in blob for tok in _WEATHER_TOKENS):
        return "weather"
    if any(c in blob for c in _CRYPTO_COINS) and any(h in t for h in _CRYPTO_PRICE_HINTS):
        return "crypto"
    return "other"


# ------------------------------ fast-settling / outcomes ------------------------------ #

def parse_resolve_ts(market_meta: dict) -> int | None:
    for k in ("endDate", "endDateIso", "end_date_iso"):
        v = market_meta.get(k)
        if not v:
            continue
        try:
            return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
        except Exception:
            continue
    return None


def is_fast_settling_at_entry(trade_ts: int, market_meta: dict) -> bool | None:
    resolve_ts = parse_resolve_ts(market_meta)
    if resolve_ts is None:
        return None
    dt = resolve_ts - trade_ts
    return 0 < dt <= FAST_SETTLING_SECONDS


def get_outcome_payout(market_meta: dict, outcome_index: int) -> float | None:
    """For a resolved market, return payout (0 or 1) for the given outcome token."""
    if not market_meta or not market_meta.get("closed"):
        return None
    outcome_prices = market_meta.get("outcomePrices")
    if isinstance(outcome_prices, str):
        try:
            outcome_prices = json.loads(outcome_prices)
        except Exception:
            outcome_prices = None
    if not outcome_prices or len(outcome_prices) <= outcome_index:
        return None
    try:
        return float(outcome_prices[outcome_index])
    except (ValueError, TypeError):
        return None


# ------------------------------ position aggregation ------------------------------ #

def compute_positions(trades: list) -> dict:
    """
    Aggregate trades into positions keyed by (condition_id, outcome_index).

    Sign convention:
      - BUY:  shares += size; usdc -= usdc_size   (you paid cash, got shares)
      - SELL: shares -= size; usdc += usdc_size   (you got cash, gave shares)

    So for a resolved position:
      realized_pnl = net_shares × outcome_payout + net_usdc
    """
    positions: dict = defaultdict(lambda: {
        "shares": 0.0, "usdc": 0.0,
        "first_ts": None, "last_ts": None,
        "trades": 0, "buys": 0, "sells": 0,
        "title": "", "slug": "", "event_slug": "",
        "category": "other",
    })

    for t in trades:
        cid = t.get("conditionId")
        oi = t.get("outcomeIndex")
        if not cid or oi is None:
            continue
        side = t.get("side", "BUY")
        size = float(t.get("size", 0) or 0)
        usdc = float(t.get("usdcSize", 0) or 0)
        ts = int(t.get("timestamp", 0) or 0)

        p = positions[(cid, oi)]
        if side == "BUY":
            p["shares"] += size
            p["usdc"] -= usdc
            p["buys"] += 1
        else:
            p["shares"] -= size
            p["usdc"] += usdc
            p["sells"] += 1
        p["trades"] += 1
        if p["first_ts"] is None or ts < p["first_ts"]:
            p["first_ts"] = ts
        if p["last_ts"] is None or ts > p["last_ts"]:
            p["last_ts"] = ts
        if not p["title"]:
            p["title"] = t.get("title", "") or ""
            p["slug"] = t.get("slug", "") or ""
            p["event_slug"] = t.get("eventSlug", "") or ""
            p["category"] = classify_category(p["title"], p["slug"], p["event_slug"])

    return dict(positions)


def settle_positions(positions: dict, meta_cache: dict) -> list:
    out = []
    for (cid, oi), p in positions.items():
        meta = meta_cache.get(cid)
        resolved = False
        realized_pnl = None
        won = None
        fast_settling = None
        if meta:
            fast_settling = is_fast_settling_at_entry(p["first_ts"], meta)
            payout = get_outcome_payout(meta, oi)
            if payout is not None:
                resolved = True
                realized_pnl = p["shares"] * payout + p["usdc"]
                won = payout > 0.5
        out.append({
            "condition_id": cid,
            "outcome_index": oi,
            "category": p["category"],
            "title": p["title"][:100],
            "trades": p["trades"],
            "buys": p["buys"],
            "sells": p["sells"],
            "net_shares": round(p["shares"], 2),
            "net_usdc": round(p["usdc"], 2),
            "first_ts": p["first_ts"],
            "last_ts": p["last_ts"],
            "resolved": resolved,
            "realized_pnl": round(realized_pnl, 2) if realized_pnl is not None else None,
            "won": won,
            "fast_settling": fast_settling,
        })
    return out


# ------------------------------ bootstrap ------------------------------ #

def bootstrap_pnl_pvalue(position_pnls: list, iters: int = BOOTSTRAP_ITERS) -> float | None:
    """
    Simple Monte Carlo bootstrap on position-level PnL.
    Returns P(resampled mean ≤ 0). Small p → strong positive signal.
    """
    vals = [p for p in position_pnls if p is not None]
    n = len(vals)
    if n < 20:
        return None
    mean = sum(vals) / n
    if mean <= 0:
        return 1.0
    hits = 0
    for _ in range(iters):
        s = random.choices(vals, k=n)
        if (sum(s) / n) <= 0:
            hits += 1
    return hits / iters


# ------------------------------ per-trader stats ------------------------------ #

def compute_trader_stats(wallet: str, name: str, trades: list,
                          meta_cache: dict, source_tags: list) -> dict:
    positions = compute_positions(trades)
    settled = settle_positions(positions, meta_cache)

    total_trades = len(trades)
    unique_markets = len(positions)
    timestamps = [t.get("timestamp", 0) or 0 for t in trades]
    timestamps = [t for t in timestamps if t]
    earliest_ts = min(timestamps) if timestamps else 0
    latest_ts = max(timestamps) if timestamps else 0
    days_trading = (latest_ts - earliest_ts) / 86400 if earliest_ts and latest_ts else 0

    cat_trades: dict = defaultdict(int)
    cat_fast_trades: dict = defaultdict(int)
    cat_resolved_trades: dict = defaultdict(int)
    cat_wins: dict = defaultdict(int)
    cat_pnl: dict = defaultdict(float)

    all_position_pnls: list = []
    fast_settling_count = 0
    resolved_trade_count = 0
    total_wins = 0
    total_realized_pnl = 0.0
    markets_meta_hit = 0

    for p in settled:
        cat = p["category"]
        cat_trades[cat] += p["trades"]
        if p["fast_settling"] is True:
            cat_fast_trades[cat] += p["trades"]
            fast_settling_count += p["trades"]
        if p["fast_settling"] is not None:
            markets_meta_hit += 1
        if p["resolved"] and p["realized_pnl"] is not None:
            resolved_trade_count += p["trades"]
            cat_resolved_trades[cat] += p["trades"]
            total_realized_pnl += p["realized_pnl"]
            cat_pnl[cat] += p["realized_pnl"]
            all_position_pnls.append(p["realized_pnl"])
            if p["won"]:
                cat_wins[cat] += p["trades"]
                total_wins += p["trades"]

    win_rate = total_wins / resolved_trade_count if resolved_trade_count else None
    pnl_per_trade = total_realized_pnl / resolved_trade_count if resolved_trade_count else None

    # Only bootstrap promising traders (save CPU)
    if fast_settling_count >= 500 and len(all_position_pnls) >= 20:
        pvalue = bootstrap_pnl_pvalue(all_position_pnls)
    else:
        pvalue = None

    # Apply filter
    counted_cats_strict: list = []
    counted_cats_honorable: list = []
    for cat in ("sports", "weather", "crypto"):
        fst = cat_fast_trades.get(cat, 0)
        pnl = cat_pnl.get(cat, 0.0)
        if fst >= 250 and pnl > 0:
            counted_cats_strict.append(cat)
        if fst >= 150:
            counted_cats_honorable.append(cat)

    passes_strict = bool(
        fast_settling_count >= 500
        and counted_cats_strict
        and pvalue is not None
        and pvalue < 0.05
    )
    passes_honorable = bool(
        fast_settling_count >= 300 and counted_cats_honorable
    )

    return {
        "wallet": wallet,
        "name": name,
        "sources": source_tags,
        "total_trades": total_trades,
        "unique_markets": unique_markets,
        "markets_meta_hit": markets_meta_hit,
        "days_trading": round(days_trading, 1),
        "earliest_date": datetime.fromtimestamp(earliest_ts, timezone.utc).date().isoformat() if earliest_ts else None,
        "latest_date": datetime.fromtimestamp(latest_ts, timezone.utc).date().isoformat() if latest_ts else None,
        "fast_settling_trade_count": fast_settling_count,
        "resolved_trade_count": resolved_trade_count,
        "total_realized_pnl": round(total_realized_pnl, 2),
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "pnl_per_trade": round(pnl_per_trade, 2) if pnl_per_trade is not None else None,
        "bootstrap_pvalue": round(pvalue, 4) if pvalue is not None else None,
        "category_trade_counts": dict(cat_trades),
        "category_fast_settling_counts": dict(cat_fast_trades),
        "category_pnl": {k: round(v, 2) for k, v in cat_pnl.items()},
        "counted_cats_strict": counted_cats_strict,
        "counted_cats_honorable": counted_cats_honorable,
        "passes_strict": passes_strict,
        "passes_honorable": passes_honorable,
    }


# ------------------------------ storage ------------------------------ #

def init_db(db_path: str):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
      CREATE TABLE IF NOT EXISTS trader_stats (
        wallet TEXT PRIMARY KEY,
        name TEXT,
        passes_strict INTEGER,
        passes_honorable INTEGER,
        fast_settling_count INTEGER,
        total_trades INTEGER,
        total_realized_pnl REAL,
        win_rate REAL,
        bootstrap_pvalue REAL,
        json TEXT NOT NULL,
        fetched_at INTEGER NOT NULL
      )
    """)
    conn.commit()
    return conn


def save_stats(conn, stats_list):
    now = int(time.time())
    conn.executemany(
        """INSERT OR REPLACE INTO trader_stats
           (wallet, name, passes_strict, passes_honorable,
            fast_settling_count, total_trades, total_realized_pnl,
            win_rate, bootstrap_pvalue, json, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [(
            s["wallet"], s["name"], int(s["passes_strict"]), int(s["passes_honorable"]),
            s["fast_settling_trade_count"], s["total_trades"], s["total_realized_pnl"],
            s["win_rate"], s["bootstrap_pvalue"],
            json.dumps(s, default=str), now,
        ) for s in stats_list]
    )
    conn.commit()


# ------------------------------ main ------------------------------ #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-per-lb", type=int, default=500)
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE)
    parser.add_argument("--db", default="data/polymarket_traders.db")
    parser.add_argument("--summary", default="data/polymarket_step2_summary.json")
    parser.add_argument("--min-recent-trades", type=int, default=50)
    parser.add_argument("--max-tier-b-pages", type=int, default=20,
                        help="Max 500-trade pages per trader in Tier B (20 = 10,000 trades cap). "
                             "Prevents HFT extremes from dominating runtime.")
    parser.add_argument("--progress-every", type=int, default=50)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    t_start = time.time()
    client = PolyClient(rate=args.rate)

    log.info("=" * 70)
    log.info("STEP 2 — universe characterization")
    log.info("limit_per_lb=%d  rate=%.1f/s  db=%s", args.limit_per_lb, args.rate, args.db)
    log.info("=" * 70)

    # Phase 1: seeds
    log.info("[Phase 1] fetching seed leaderboards")
    universe: dict = {}
    for kind, window in [("profit", "30d"), ("profit", "7d"), ("volume", "30d"), ("volume", "7d")]:
        entries = client.leaderboard(kind, window, args.limit_per_lb)
        log.info("  %s/%s → %d entries", kind, window, len(entries))
        for e in entries:
            w = e.get("proxyWallet")
            if not w:
                continue
            if w not in universe:
                universe[w] = {
                    "wallet": w,
                    "pseudonym": e.get("pseudonym") or e.get("name") or "?",
                    "sources": {},
                }
            universe[w]["sources"][f"{kind}_{window}"] = e.get("amount")
    log.info("  unique traders after dedupe: %d", len(universe))

    if not universe:
        log.error("FATAL: no traders in universe, aborting")
        return 1

    # Phase 2: Tier A headline pass
    log.info("[Phase 2] Tier A headline pass (most recent 500 trades per trader)")
    tier_a: dict = {}
    for i, (w, info) in enumerate(universe.items()):
        try:
            tier_a[w] = client.activity_page(w, limit=500)
        except Exception as ex:
            log.warning("  Tier A exception for %s: %s", w[:12], ex)
            tier_a[w] = []
        if (i + 1) % args.progress_every == 0 or i == len(universe) - 1:
            log.info("  Tier A %d/%d  (req=%d 429=%d err=%d elapsed=%.0fs)",
                     i + 1, len(universe),
                     client.stats["requests"], client.stats["429s"],
                     client.stats["errors"], time.time() - t_start)

    promising = [w for w, tr in tier_a.items() if len(tr) >= args.min_recent_trades]
    log.info("  %d/%d traders cleared Tier A cutoff (≥%d recent trades)",
             len(promising), len(universe), args.min_recent_trades)

    # Mid-flight rate check: if Tier A had 0 429s, bump rate
    if client.stats["429s"] == 0 and args.rate < 8.0:
        new_rate = min(args.rate * 2, 10.0)
        log.info("  no 429s in Tier A — bumping rate %.1f → %.1f", args.rate, new_rate)
        client.rl.set_rate(new_rate)

    # Phase 3: Tier B full activity for promising traders
    log.info("[Phase 3] Tier B — full activity pagination for %d traders", len(promising))
    full_activity: dict = {}
    for i, w in enumerate(promising):
        try:
            full_activity[w] = client.all_activity(w, max_pages=args.max_tier_b_pages)
        except Exception as ex:
            log.warning("  Tier B exception for %s: %s", w[:12], ex)
            full_activity[w] = tier_a.get(w, [])
        if (i + 1) % 20 == 0 or i == len(promising) - 1:
            log.info("  Tier B %d/%d last=%s (n_trades=%d, req=%d elapsed=%.0fs)",
                     i + 1, len(promising), w[:12],
                     len(full_activity[w]),
                     client.stats["requests"], time.time() - t_start)

    for w in universe:
        if w not in full_activity:
            full_activity[w] = tier_a.get(w, [])

    # Phase 4: metadata cache
    log.info("[Phase 4] metadata cache build")
    all_cids: set = set()
    for trades in full_activity.values():
        for t in trades:
            cid = t.get("conditionId")
            if cid:
                all_cids.add(cid)
    log.info("  %d unique condition_ids to fetch", len(all_cids))
    meta_cache = client.markets_by_cids(list(all_cids))
    hit_rate = 100.0 * len(meta_cache) / max(len(all_cids), 1)
    log.info("  metadata hit: %d/%d (%.1f%%)  req=%d elapsed=%.0fs",
             len(meta_cache), len(all_cids), hit_rate,
             client.stats["requests"], time.time() - t_start)

    # Phase 5: compute stats
    log.info("[Phase 5] computing per-trader stats")
    all_stats: list = []
    for w, info in universe.items():
        trades = full_activity.get(w, [])
        if not trades:
            continue
        source_tags = list(info["sources"].keys())
        try:
            s = compute_trader_stats(w, info["pseudonym"], trades, meta_cache, source_tags)
            s["leaderboard_sources"] = info["sources"]
            all_stats.append(s)
        except Exception as ex:
            log.warning("  stats exception for %s: %s", w[:12], ex)
    log.info("  computed stats for %d traders", len(all_stats))

    # Phase 6: save + summary
    log.info("[Phase 6] saving to SQLite + reporting")
    conn = init_db(args.db)
    save_stats(conn, all_stats)

    funnel = {
        "universe_total": len(universe),
        "tier_a_passed": len(promising),
        "has_any_resolved_pnl": sum(1 for s in all_stats if s["resolved_trade_count"] > 0),
        "with_ge_500_total_trades": sum(1 for s in all_stats if s["total_trades"] >= 500),
        "with_ge_500_fast_settling": sum(1 for s in all_stats if s["fast_settling_trade_count"] >= 500),
        "with_ge_300_fast_settling": sum(1 for s in all_stats if s["fast_settling_trade_count"] >= 300),
        "has_strict_counted_category": sum(1 for s in all_stats if s["counted_cats_strict"]),
        "has_honorable_counted_category": sum(1 for s in all_stats if s["counted_cats_honorable"]),
        "passes_strict": sum(1 for s in all_stats if s["passes_strict"]),
        "passes_honorable": sum(1 for s in all_stats if s["passes_honorable"]),
    }

    elapsed = time.time() - t_start

    log.info("=" * 70)
    log.info("SUMMARY — elapsed=%.0fs  req=%d  429=%d  err=%d",
             elapsed, client.stats["requests"], client.stats["429s"], client.stats["errors"])
    log.info("=" * 70)
    log.info("Filter funnel:")
    for k, v in funnel.items():
        log.info("  %-32s  %5d", k, v)

    summary = {
        "started_utc": datetime.utcfromtimestamp(t_start).isoformat() + "Z",
        "elapsed_seconds": round(elapsed, 1),
        "api_stats": dict(client.stats),
        "params": vars(args),
        "funnel": funnel,
        "traders": all_stats,
    }
    os.makedirs(os.path.dirname(args.summary) or ".", exist_ok=True)
    with open(args.summary, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    log.info("summary → %s", args.summary)

    # Top strict survivors
    strict = sorted([s for s in all_stats if s["passes_strict"]],
                    key=lambda s: -(s["total_realized_pnl"] or 0))
    if strict:
        log.info("\nStrict-tier survivors (top 10 by realized PnL):")
        for s in strict[:10]:
            log.info("  %-22s  pnl=%8.0f  wr=%s  fast=%5d  cats=%s",
                     s["name"][:22], s["total_realized_pnl"] or 0,
                     f"{s['win_rate']:.2f}" if s["win_rate"] is not None else "  -",
                     s["fast_settling_trade_count"],
                     s["counted_cats_strict"])
    else:
        log.info("\nNo strict-tier survivors. Top honorable mentions (top 10 by realized PnL):")
        hon = sorted([s for s in all_stats if s["passes_honorable"]],
                     key=lambda s: -(s["total_realized_pnl"] or 0))
        for s in hon[:10]:
            log.info("  %-22s  pnl=%8.0f  wr=%s  fast=%5d  cats=%s",
                     s["name"][:22], s["total_realized_pnl"] or 0,
                     f"{s['win_rate']:.2f}" if s["win_rate"] is not None else "  -",
                     s["fast_settling_trade_count"],
                     s["counted_cats_honorable"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
