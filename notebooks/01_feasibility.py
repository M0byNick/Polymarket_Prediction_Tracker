#!/usr/bin/env python3
"""
Step 1 — Feasibility spike for Polymarket trader portfolio analysis.

Goals (in priority order):
  1. Confirm leaderboard endpoint returns top traders by all-time profit & volume.
  2. Confirm /activity endpoint returns paginated trade history by wallet.
  3. Confirm we can join trades to market metadata via Gamma /markets.
  4. Test whether CLOB /prices-history returns market prices at trade timestamps
     (the "can we reconstruct entry-time market prices?" technical risk).
  5. Measure observed rate limits (429s per N requests, effective throughput).
  6. Produce a small per-trader sample table to show the user what's available.

Throwaway script. No DB writes. Output to stdout + JSON dump at /tmp.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

LB_API = "https://lb-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

REQUEST_DELAY = 0.3   # conservative 3.3 req/s baseline
TIMEOUT = 20
MAX_ACTIVITY_PAGES = 100  # cap at 50,000 trades per wallet

stats = {
    "requests": 0,
    "rate_limited_429s": 0,
    "errors": 0,
    "started": time.time(),
    "by_endpoint": defaultdict(int),
}


def _endpoint_key(url: str) -> str:
    # cheap label — just the host + first path segment
    try:
        host = url.split("/")[2]
        path = "/" + url.split("/", 3)[3].split("?")[0].split("/")[0]
        return f"{host}{path}"
    except Exception:
        return url


def get_json(url, params=None, retries=3):
    stats["requests"] += 1
    stats["by_endpoint"][_endpoint_key(url)] += 1
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 429:
                stats["rate_limited_429s"] += 1
                wait = 2 ** (attempt + 1)
                print(f"  [429] {url} — backing off {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            stats["errors"] += 1
            if attempt == retries - 1:
                print(f"  ERROR {url}: {e}", file=sys.stderr)
                return None
            time.sleep(2 ** attempt)
        except ValueError as e:
            print(f"  JSON decode error {url}: {e}", file=sys.stderr)
            return None
    return None


def get_leaderboard(kind: str, window: str = "all", limit: int = 5):
    """kind = 'profit' or 'volume'"""
    return get_json(f"{LB_API}/{kind}", params={"window": window, "limit": limit})


def get_activity_page(wallet: str, limit: int = 500, offset: int = 0, end: int | None = None):
    params = {"user": wallet, "type": "TRADE", "limit": limit}
    if offset:
        params["offset"] = offset
    if end is not None:
        params["end"] = end
    return get_json(f"{DATA_API}/activity", params=params)


def get_all_activity(wallet: str, max_pages: int = MAX_ACTIVITY_PAGES) -> list:
    """
    Paginate through all TRADE activity for a wallet.

    Try offset-based pagination first. If that doesn't advance, fall back to
    timestamp-based pagination using the `end` parameter.
    """
    all_trades: list = []
    offset = 0

    for page in range(max_pages):
        batch = get_activity_page(wallet, limit=500, offset=offset)
        if not batch:
            break
        all_trades.extend(batch)
        if len(batch) < 500:
            break
        offset += len(batch)
        time.sleep(REQUEST_DELAY)

    return all_trades


def get_market_meta(condition_id: str):
    result = get_json(
        f"{GAMMA_API}/markets",
        params={"condition_ids": condition_id, "limit": 1},
    )
    if isinstance(result, list) and result:
        return result[0]
    if isinstance(result, dict):
        if "data" in result and result["data"]:
            return result["data"][0]
        return result
    return None


def get_price_history_sample(token_id: str):
    """Try to fetch price history for a token. Return (bool_ok, n_points)."""
    result = get_json(
        f"{CLOB_API}/prices-history",
        params={"market": token_id, "interval": "1d", "fidelity": 1},
    )
    if not result:
        return False, 0
    history = result.get("history") if isinstance(result, dict) else None
    if history is None and isinstance(result, list):
        history = result
    if history:
        return True, len(history)
    return False, 0


def summarize_trader(wallet: str, trades: list, name: str = "") -> dict:
    s: dict = {
        "wallet": wallet,
        "name": name,
        "total_trades": len(trades),
        "earliest_ts": None,
        "latest_ts": None,
        "earliest_date": None,
        "latest_date": None,
        "days_active": 0.0,
        "unique_markets": 0,
        "sample_trade_keys": [],
        "sample_market_meta_ok": 0,
        "sample_market_meta_tried": 0,
        "sample_price_history_ok": 0,
        "sample_price_history_tried": 0,
        "fast_settling_sampled": 0,
        "categories_sampled": {},
    }
    if not trades:
        return s

    # Show the shape of a trade object (for us to see what fields are available)
    s["sample_trade_keys"] = sorted(list(trades[0].keys())) if trades else []

    # Timestamps — try common field names
    def trade_ts(t):
        for k in ("timestamp", "ts", "createdAt", "time"):
            if k in t and t[k]:
                v = t[k]
                if isinstance(v, (int, float)):
                    return int(v)
                if isinstance(v, str):
                    try:
                        return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
                    except Exception:
                        pass
        return None

    tss = [trade_ts(t) for t in trades]
    tss = [t for t in tss if t]
    if tss:
        s["earliest_ts"] = min(tss)
        s["latest_ts"] = max(tss)
        s["earliest_date"] = datetime.fromtimestamp(s["earliest_ts"], timezone.utc).date().isoformat()
        s["latest_date"] = datetime.fromtimestamp(s["latest_ts"], timezone.utc).date().isoformat()
        s["days_active"] = round((s["latest_ts"] - s["earliest_ts"]) / 86400, 1)

    # Unique markets
    def trade_cid(t):
        for k in ("conditionId", "condition_id", "marketId", "market_id", "market"):
            if k in t and t[k]:
                return t[k]
        return None

    cids = [trade_cid(t) for t in trades]
    cids = [c for c in cids if c]
    unique_cids = list(dict.fromkeys(cids))  # preserve order, dedupe
    s["unique_markets"] = len(unique_cids)

    # Sample up to 5 markets: pull metadata, classify fast-settling, try price history
    sample_cids = unique_cids[:5]
    categories = defaultdict(int)
    for cid in sample_cids:
        s["sample_market_meta_tried"] += 1
        meta = get_market_meta(cid)
        time.sleep(REQUEST_DELAY)
        if not meta:
            continue
        s["sample_market_meta_ok"] += 1

        # Pull category
        cat = (
            meta.get("category")
            or meta.get("groupItemTitle")
            or meta.get("eventSlug", "unknown")
        )
        categories[str(cat)[:30]] += 1

        # Fast-settling check: market duration, if creation + end dates available
        start_date = meta.get("startDate") or meta.get("startDateIso")
        end_date = meta.get("endDate") or meta.get("endDateIso")
        if start_date and end_date:
            try:
                sd = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                ed = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                duration_h = (ed - sd).total_seconds() / 3600
                if 0 < duration_h <= 72:
                    s["fast_settling_sampled"] += 1
            except Exception:
                pass

        # Price history check
        tokens = meta.get("clobTokenIds")
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except Exception:
                tokens = []
        if tokens and len(tokens) > 0:
            s["sample_price_history_tried"] += 1
            ok, n = get_price_history_sample(tokens[0])
            if ok:
                s["sample_price_history_ok"] += 1
            time.sleep(REQUEST_DELAY)

    s["categories_sampled"] = dict(categories)
    return s


def main():
    print("=" * 70)
    print("POLYMARKET FEASIBILITY SPIKE — 10 traders")
    print("=" * 70)

    results: dict = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "traders": [],
        "stats": {},
    }

    # ------------------------------------------------------------------
    # Step A: leaderboards
    # ------------------------------------------------------------------
    print("\n[A] Fetching top-5 profit (window=all) and top-5 volume (window=all)...")
    profit_lb = get_leaderboard("profit", window="all", limit=5) or []
    volume_lb = get_leaderboard("volume", window="all", limit=5) or []

    print(f"    profit endpoint returned {len(profit_lb)} entries")
    print(f"    volume endpoint returned {len(volume_lb)} entries")

    if not profit_lb and not volume_lb:
        print("\n  FATAL: leaderboards empty — cannot proceed.")
        return 1

    # Dedupe by wallet
    seen = set()
    universe: list = []
    for src, entries in (("profit", profit_lb), ("volume", volume_lb)):
        for e in entries:
            w = e.get("proxyWallet")
            if w and w not in seen:
                seen.add(w)
                universe.append({
                    "source": src,
                    "wallet": w,
                    "pseudonym": e.get("pseudonym") or e.get("name") or "?",
                    "amount": e.get("amount"),
                })

    print(f"\n    {len(universe)} unique traders after dedupe:")
    for i, t in enumerate(universe):
        amt = t["amount"] or 0
        print(f"      {i+1}. [{t['source']:6}] {t['pseudonym'][:25]:25} "
              f"${amt:>14,.0f}  {t['wallet']}")

    # ------------------------------------------------------------------
    # Step B: pull full activity for each
    # ------------------------------------------------------------------
    print(f"\n[B] Pulling full trade history for {len(universe)} traders...")
    for i, t in enumerate(universe):
        t0 = time.time()
        print(f"\n  [{i+1}/{len(universe)}] {t['pseudonym']} ({t['wallet']})")
        trades = get_all_activity(t["wallet"])
        elapsed = time.time() - t0
        print(f"    -> {len(trades)} trades in {elapsed:.1f}s")

        summary = summarize_trader(t["wallet"], trades, t["pseudonym"])
        summary["source"] = t["source"]
        summary["leaderboard_amount"] = t["amount"]
        summary["fetch_seconds"] = round(elapsed, 1)
        results["traders"].append(summary)

    # ------------------------------------------------------------------
    # Step C: report
    # ------------------------------------------------------------------
    stats["elapsed"] = round(time.time() - stats["started"], 1)
    stats["by_endpoint"] = dict(stats["by_endpoint"])
    results["stats"] = dict(stats)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\n  Total requests : {stats['requests']}")
    print(f"  Rate-limited   : {stats['rate_limited_429s']}")
    print(f"  Errors         : {stats['errors']}")
    print(f"  Wall-clock     : {stats['elapsed']}s")
    eff = stats['requests'] / max(stats['elapsed'], 1)
    print(f"  Effective rate : {eff:.1f} req/s")
    print(f"  By endpoint    : {dict(stats['by_endpoint'])}")

    print("\n  Per-trader:")
    header = (
        f"    {'name':<22} {'src':<7} {'trades':>7} {'mkts':>6} "
        f"{'days':>7} {'meta':>5} {'phist':>6} {'fast':>5}"
    )
    print(header)
    print("    " + "-" * (len(header) - 4))
    for t in results["traders"]:
        meta = f"{t['sample_market_meta_ok']}/{t['sample_market_meta_tried']}"
        phist = f"{t['sample_price_history_ok']}/{t['sample_price_history_tried']}"
        print(
            f"    {t['name'][:22]:<22} "
            f"{t['source']:<7} "
            f"{t['total_trades']:>7} "
            f"{t['unique_markets']:>6} "
            f"{t['days_active']:>7} "
            f"{meta:>5} "
            f"{phist:>6} "
            f"{t['fast_settling_sampled']:>5}"
        )

    print("\n  Sampled categories across all traders:")
    all_cats: dict = defaultdict(int)
    for t in results["traders"]:
        for k, v in t.get("categories_sampled", {}).items():
            all_cats[k] += v
    for k, v in sorted(all_cats.items(), key=lambda x: -x[1]):
        print(f"    {k[:40]:<40} {v}")

    # Save dump for deeper inspection
    out = "/tmp/poly_spike_output.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Full dump → {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
