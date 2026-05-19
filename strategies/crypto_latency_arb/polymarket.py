"""
Polymarket discovery + CLOB top-of-book client for crypto-binary markets.

DISCOVERY (pre-reg §2.1, §2.2)
  Polymarket runs short-DTE crypto up/down markets under the Gamma `crypto`
  tag. As of 2026-05-18 the in-scope market families observed live are:

    family          example slug                              resolution time
    --------------- ----------------------------------------- ---------------
    updown_5m       btc-updown-5m-1779148500                   slug unix ts
    updown_15m      eth-updown-15m-1779147900                  slug unix ts
    updown_hourly   bitcoin-up-or-down-may-18-2026-9pm-et      market endDate
    above_hourly    ethereum-above-2150-on-may-18-2026-9pm-et  market endDate

  - `updown_5m` / `updown_15m`: a relative "Up vs Down" market resolving on
    whether close >= open over the window. The window-END unix timestamp is
    the integer suffix of the slug; the Gamma `endDate` field on these
    markets is a stale series-container value (always midnight UTC) and is
    NOT used. Resolution source: Chainlink price stream.
  - `updown_hourly`: a relative "Up or Down" market over a 1-hour Binance
    BTC/USDT candle. The Gamma market `endDate` IS the true resolution time.
    Resolution source: Binance.
  - `above_hourly`: a fixed-strike "X above $STRIKE at HH:MM" market. The
    strike is in the Gamma `groupItemTitle` field (e.g. "2,150"); `endDate`
    is the true resolution time. Resolution source: Binance.

  Discovery queries the Gamma `/events` endpoint paged by `end_date_min` and
  `order=endDate` (NOT `closed=false` — on these recurring markets the
  `closed`/`active` flags are unreliable, exactly as documented in the
  weather collector's polymarket.py; the canonical `markets` endpoint with
  `active=true&closed=false` returns months-stale never-resolved markets).
  We filter client-side on the *true* resolution timestamp.

HORIZON BUCKETING (pre-reg §2.2)
  The pre-reg defines two horizons: "hourly" (resolve - start <= 70 min) and
  "15-minute" (resolve - start <= 20 min). The `updown_5m` family satisfies
  the 15-minute bound (5 < 20 min) and is bucketed as `15min`; the per-market
  `family` column preserves the finer 5m/15m distinction so the holdout
  analysis can split precisely. Markets whose lifetime fits neither bound
  (e.g. `updown_4h`) are out of scope and dropped.

CLOB TOP-OF-BOOK
  For each in-scope market we fetch the order book for the YES and NO CLOB
  tokens via the CLOB `/books` batch endpoint (POST). We record best bid,
  best ask, and inside sizes for both tokens — the raw microstructure the
  pre-reg's §4 filters and the divergence measurement need.

OBSERVE-ONLY: this module reads public market data only. No orders.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

log = logging.getLogger("crypto_arb.polymarket")

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TIMEOUT = 30
USER_AGENT = "Tracker_Poly crypto_latency_arb research (Polymarket)"

# Gamma tag carrying the short-DTE crypto up/down + strike binary markets.
CRYPTO_TAG = "crypto"

# Pre-reg §2.1 frozen underlyings. Slug-prefix -> canonical symbol.
# Both the short prefix (btc-) used by the updown series and the long prefix
# (bitcoin-) used by the up-or-down / above series are mapped.
_UNDERLYING_BY_PREFIX: dict[str, str] = {
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL",
    "xrp": "XRP",
}
IN_SCOPE_UNDERLYINGS: tuple[str, ...] = ("BTC", "ETH", "SOL", "XRP")

# Pre-reg §2.2 horizon lifetime bounds (resolve_ts - start_ts), in seconds.
HOURLY_MAX_LIFETIME_S = 70 * 60
FIFTEEN_MIN_MAX_LIFETIME_S = 20 * 60

# Capture-horizon — a COLLECTOR-EFFICIENCY parameter, NOT a pre-reg
# hyperparameter. Polymarket pre-creates short-DTE crypto markets hours in
# advance, so an unbounded discovery sweeps in ~600 markets/cycle, most
# resolving 2+ hours out. The pre-reg §5 entry window is at most 30 minutes
# before resolution; a market resolving hours away is not yet tradeable and
# its book is still being assembled (pre-reg §5 book-fill-saturation note).
# We therefore only DENSELY snapshot (every 30s) markets resolving within
# this horizon. The default 90 min comfortably covers the pre-reg's <=30-min
# entry window plus 60 min of pre-window margin — the collector still sees a
# market well before it becomes tradeable. This bounds DB growth to a
# disk-safe rate (~1 GB -> ~0.1 GB/day at 30s cadence) WITHOUT touching any
# pre-registered parameter: the strike, the entry window, the model, the
# divergence threshold and the universe are all unchanged. Override with the
# CRYPTO_ARB_CAPTURE_HORIZON_MIN environment variable if needed.
DEFAULT_CAPTURE_HORIZON_S = 90 * 60

# `<sym>-updown-<window>-<unixts>` — relative up/down, ts suffix is window end.
_UPDOWN_RE = re.compile(r"^(btc|eth|sol|xrp)-updown-(\d+)(m|h)-(\d+)$")
# `<name>-up-or-down-...` — relative hourly up/down, endDate is resolution.
_UPORDOWN_RE = re.compile(r"^(bitcoin|ethereum|solana|xrp)-up-or-down-")
# `<name>-above-...` — fixed-strike hourly, endDate is resolution, strike in
# groupItemTitle.
_ABOVE_RE = re.compile(r"^(bitcoin|ethereum|solana|xrp)-above-")

# Strike string -> float. Polymarket groupItemTitle is e.g. "2,150" / "75,200"
# / "1.39" (XRP). The slug strike token is unpunctuated, e.g. "75200". Both
# spellings must parse: a comma-grouped form ("75,200") AND a plain run of
# digits ("75200") — the plain-integer alternative must come FIRST so the
# regex does not stop after the leading 3 digits of an ungrouped number.
_STRIKE_NUM_RE = re.compile(
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
)


@dataclass(frozen=True)
class TokenBook:
    """Top-of-book for a single CLOB token."""
    token_id: str
    best_bid: Optional[float]
    best_ask: Optional[float]
    bid_size: Optional[float]
    ask_size: Optional[float]

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0


@dataclass(frozen=True)
class CryptoMarket:
    """An in-scope Polymarket crypto-binary market with parsed metadata."""
    condition_id: str
    market_slug: str
    question: Optional[str]
    underlying: str            # 'BTC' | 'ETH' | 'SOL' | 'XRP'
    family: str                # 'updown_5m'|'updown_15m'|'updown_hourly'|'above_hourly'
    horizon: str               # 'hourly' | '15min'
    market_kind: str           # 'relative' | 'strike'
    strike: Optional[float]    # parsed strike for 'strike' markets, else None
    resolve_ts: int            # unix seconds — true resolution time
    start_ts: Optional[int]    # unix seconds — market creation time
    yes_token: str             # CLOB token id for YES/Up
    no_token: str              # CLOB token id for NO/Down
    resolution_source: Optional[str]


def _parse_iso_ts(raw) -> Optional[int]:
    """Parse a Gamma ISO-8601 timestamp into unix seconds."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def _parse_strike(raw) -> Optional[float]:
    """Parse a strike from a groupItemTitle / slug fragment, e.g. '2,150'."""
    if raw is None:
        return None
    m = _STRIKE_NUM_RE.search(str(raw))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _coerce_tokens(raw) -> Optional[list[str]]:
    """clobTokenIds arrives as a JSON-encoded list string. Return [yes, no]."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if isinstance(raw, list) and len(raw) >= 2:
        return [str(raw[0]), str(raw[1])]
    return None


def _classify_market(market_slug: str, gamma_market: dict,
                      now_ts: int) -> Optional[CryptoMarket]:
    """
    Parse one Gamma market dict into a CryptoMarket, or return None if the
    market is not in the pre-reg §2 universe (wrong family, wrong horizon,
    wrong underlying, already resolved, or unparseable).
    """
    tokens = _coerce_tokens(gamma_market.get("clobTokenIds"))
    if not tokens:
        return None
    cond_id = gamma_market.get("conditionId") or ""
    if not cond_id:
        return None
    question = gamma_market.get("question")
    resolution_source = _resolution_source_hint(gamma_market.get("description"))
    start_ts = _parse_iso_ts(gamma_market.get("startDate"))
    end_ts_field = _parse_iso_ts(gamma_market.get("endDate"))

    # --- family 1/2: <sym>-updown-<n>{m,h}-<unixts> -------------------------
    m = _UPDOWN_RE.match(market_slug)
    if m:
        sym = _UNDERLYING_BY_PREFIX.get(m.group(1))
        if sym is None:
            return None
        win_n = int(m.group(2))
        win_unit = m.group(3)
        win_minutes = win_n * (60 if win_unit == "h" else 1)
        # The slug's unix ts suffix is the window-end = true resolution time.
        resolve_ts = int(m.group(4))
        # Horizon bucket by window length (pre-reg §2.2 bounds).
        win_s = win_minutes * 60
        if win_s <= FIFTEEN_MIN_MAX_LIFETIME_S:
            horizon = "15min"
            family = f"updown_{win_n}m" if win_unit == "m" else f"updown_{win_n}h"
        elif win_s <= HOURLY_MAX_LIFETIME_S:
            horizon = "hourly"
            family = "updown_hourly"
        else:
            # e.g. updown_4h — outside both pre-reg horizon bounds.
            return None
        if resolve_ts <= now_ts:
            return None
        return CryptoMarket(
            condition_id=cond_id, market_slug=market_slug, question=question,
            underlying=sym, family=family, horizon=horizon,
            market_kind="relative", strike=None, resolve_ts=resolve_ts,
            start_ts=start_ts, yes_token=tokens[0], no_token=tokens[1],
            resolution_source=resolution_source,
        )

    # --- family 3: <name>-up-or-down-... (relative hourly) ------------------
    if _UPORDOWN_RE.match(market_slug):
        prefix = market_slug.split("-", 1)[0]
        sym = _UNDERLYING_BY_PREFIX.get(prefix)
        if sym is None or end_ts_field is None:
            return None
        resolve_ts = end_ts_field
        if resolve_ts <= now_ts:
            return None
        # Horizon bound check on the market lifetime.
        if start_ts is not None and (resolve_ts - start_ts) > HOURLY_MAX_LIFETIME_S:
            # Series-container start can predate the real 1h window; the
            # hourly up/down market resolves a single 1h candle, so accept it
            # by family even when the recorded startDate is the series open.
            pass
        return CryptoMarket(
            condition_id=cond_id, market_slug=market_slug, question=question,
            underlying=sym, family="updown_hourly", horizon="hourly",
            market_kind="relative", strike=None, resolve_ts=resolve_ts,
            start_ts=start_ts, yes_token=tokens[0], no_token=tokens[1],
            resolution_source=resolution_source,
        )

    # --- family 4: <name>-above-<strike>-... (fixed-strike hourly) ----------
    if _ABOVE_RE.match(market_slug):
        prefix = market_slug.split("-", 1)[0]
        sym = _UNDERLYING_BY_PREFIX.get(prefix)
        if sym is None or end_ts_field is None:
            return None
        resolve_ts = end_ts_field
        if resolve_ts <= now_ts:
            return None
        strike = _parse_strike(gamma_market.get("groupItemTitle"))
        if strike is None:
            # Fallback: parse the strike token out of the slug
            # (e.g. 'bitcoin-above-75200-on-...').
            sm = re.match(r"^[a-z]+-above-([\d,\.]+)-on-", market_slug)
            strike = _parse_strike(sm.group(1)) if sm else None
        if strike is None:
            return None
        return CryptoMarket(
            condition_id=cond_id, market_slug=market_slug, question=question,
            underlying=sym, family="above_hourly", horizon="hourly",
            market_kind="strike", strike=strike, resolve_ts=resolve_ts,
            start_ts=start_ts, yes_token=tokens[0], no_token=tokens[1],
            resolution_source=resolution_source,
        )

    return None


def _resolution_source_hint(description) -> Optional[str]:
    """Extract a short resolution-source label from the market rules text."""
    if not description:
        return None
    d = str(description)
    low = d.lower()
    if "chainlink" in low:
        # e.g. "BTC/USD data stream"
        m = re.search(r"([A-Z]{2,5}/USD)", d)
        return f"Chainlink {m.group(1)}" if m else "Chainlink"
    if "binance" in low:
        m = re.search(r"([A-Z]{2,5}/USDT)", d)
        return f"Binance {m.group(1)}" if m else "Binance"
    if "coinbase" in low:
        return "Coinbase"
    return None


def discover_markets(session: Optional[requests.Session] = None,
                     max_pages: int = 12,
                     capture_horizon_s: int = DEFAULT_CAPTURE_HORIZON_S
                     ) -> list[CryptoMarket]:
    """
    Discover in-scope Polymarket crypto-binary markets (pre-reg §2 universe).

    Pages the Gamma `/events` endpoint by ascending endDate starting at
    today-or-later, classifies each contained market, and returns the parsed
    in-scope CryptoMarket list. De-duplicates by condition_id.

    `capture_horizon_s` is a COLLECTOR-EFFICIENCY bound (see
    DEFAULT_CAPTURE_HORIZON_S): markets resolving further than this from now
    are dropped from dense snapshotting. It does NOT alter the pre-reg §2
    universe — every market kept is fully in-universe; markets dropped here
    are simply not yet near their <=30-min entry window and would otherwise
    bloat the DB. Pass a very large value to disable the bound entirely.
    """
    s = session or requests
    now_dt = datetime.now(timezone.utc)
    now_ts = int(now_dt.timestamp())
    end_date_min = now_dt.strftime("%Y-%m-%dT00:00:00Z")
    max_resolve_ts = now_ts + capture_horizon_s

    out: dict[str, CryptoMarket] = {}
    page = 100
    for page_idx in range(max_pages):
        r = s.get(
            f"{GAMMA}/events",
            params={
                "limit": page,
                "offset": page_idx * page,
                "tag_slug": CRYPTO_TAG,
                "order": "endDate",
                "ascending": "true",
                "end_date_min": end_date_min,
            },
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        for event in batch:
            for gm in (event.get("markets") or ()):
                slug = gm.get("slug") or ""
                if not slug:
                    continue
                cm = _classify_market(slug, gm, now_ts)
                if cm is None or cm.condition_id in out:
                    continue
                # Collector-efficiency horizon: skip markets still far from
                # their entry window (pre-reg universe membership unchanged).
                if cm.resolve_ts > max_resolve_ts:
                    continue
                out[cm.condition_id] = cm
        if len(batch) < page:
            break

    return list(out.values())


def fetch_books(token_ids: list[str],
                session: Optional[requests.Session] = None,
                chunk: int = 50) -> dict[str, TokenBook]:
    """
    Fetch CLOB top-of-book for many tokens via the batch `/books` endpoint.

    Returns a {token_id: TokenBook} map. Tokens that error or return an empty
    book are simply absent from the map; the caller treats a missing token as
    "no quote this snapshot" and still writes a row with NULL prices.
    """
    s = session or requests
    out: dict[str, TokenBook] = {}
    if not token_ids:
        return out
    uniq = list(dict.fromkeys(token_ids))
    for i in range(0, len(uniq), chunk):
        block = uniq[i:i + chunk]
        try:
            r = s.post(
                f"{CLOB}/books",
                json=[{"token_id": t} for t in block],
                timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as ex:
            log.warning("CLOB /books chunk failed (%d tokens): %s",
                        len(block), ex)
            continue
        if not isinstance(payload, list):
            continue
        for entry in payload:
            tok = str(entry.get("asset_id") or entry.get("token_id") or "")
            if not tok:
                continue
            out[tok] = _parse_book(tok, entry)
    return out


def _parse_book(token_id: str, entry: dict) -> TokenBook:
    """
    Parse a CLOB /books entry into top-of-book.

    CLOB returns `bids` ascending (worst -> best) and `asks` descending
    (worst -> best). The best bid is the *highest* bid price; the best ask is
    the *lowest* ask price. We scan rather than positionally index to be
    robust to ordering.
    """
    bids = entry.get("bids") or []
    asks = entry.get("asks") or []

    best_bid = None
    best_bid_size = None
    for lvl in bids:
        try:
            px = float(lvl["price"])
            sz = float(lvl["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if best_bid is None or px > best_bid:
            best_bid = px
            best_bid_size = sz

    best_ask = None
    best_ask_size = None
    for lvl in asks:
        try:
            px = float(lvl["price"])
            sz = float(lvl["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if best_ask is None or px < best_ask:
            best_ask = px
            best_ask_size = sz

    return TokenBook(
        token_id=token_id,
        best_bid=best_bid, best_ask=best_ask,
        bid_size=best_bid_size, ask_size=best_ask_size,
    )
