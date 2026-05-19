"""
Centralized-exchange spot reference for the crypto-binary latency arb
collector (pre-reg §2.3).

REFERENCE EXCHANGE ORDERING (frozen, pre-reg §2.3)
    BTC/ETH/SOL/XRP -> Binance Spot primary, Coinbase fallback.

OPERATIONAL REALITY
    api.binance.com is geo-restricted and returns HTTP 451 ("Service
    unavailable from a restricted location") from a US IP. The pre-reg §2.3
    explicitly authorises Coinbase as the fallback when Binance returns
    429/451. Verified on 2026-05-18 from the operator's US-based Mac:
    Binance bookTicker -> 451, Coinbase ticker -> 200. So in practice the
    Coinbase fallback fires on essentially every call. This module therefore
    builds the Coinbase path to be robust and primary-in-practice while
    still ATTEMPTING Binance first each call (so the picture self-corrects
    if the collector is ever moved to a non-restricted IP), and logs the
    `source` actually used per row (pre-reg §2.3: "log which source was
    used per row").

WHAT IS CAPTURED
    Per underlying, per snapshot:
      - top-of-book bid / ask / mid + last trade price on the chosen CEX
      - the exchange-reported timestamp where available (Coinbase ticker)
      - a trailing-60-minute annualized realized-vol estimate computed from
        1-minute close-to-close log returns (pre-reg §3.2). On Binance this
        is GET /api/v3/klines?interval=1m&limit=60; on Coinbase it is
        GET /products/<p>/candles?granularity=60 (1-minute candles).

VOL ESTIMATOR (pre-reg §3.2)
    sigma_ann = sqrt(252 * 24 * 60) * stddev(log(C_{i+1} / C_i))
    over the trailing 60 one-minute bars. If fewer than 30 bars are
    available the estimate is returned as None (pre-reg §3.2 missing-data
    rule — the downstream model snapshot is then logged with a note rather
    than a fabricated sigma).

OBSERVE-ONLY: public market-data endpoints only. No keys, no orders.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("crypto_arb.cex_spot")

BINANCE_BASE = "https://api.binance.com"
COINBASE_BASE = "https://api.exchange.coinbase.com"
TIMEOUT = 15
USER_AGENT = "Tracker_Poly crypto_latency_arb research"

# pre-reg §2.3 frozen ticker map.
BINANCE_SYMBOL: dict[str, str] = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT",
}
COINBASE_PRODUCT: dict[str, str] = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "XRP": "XRP-USD",
}

# pre-reg §3.2 vol-estimator constants (LOCKED).
VOL_WINDOW_BARS = 60                       # trailing 60 one-minute bars
VOL_MIN_BARS = 30                          # below this -> sigma is None
_ANNUALIZE = math.sqrt(252.0 * 24.0 * 60.0)


@dataclass(frozen=True)
class SpotQuote:
    """A CEX spot reference for one underlying at one snapshot."""
    underlying: str
    source: str                # 'binance' | 'coinbase'
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    exchange_ts_ms: Optional[int]
    sigma_ann: Optional[float]      # trailing-60m annualized realized vol
    n_vol_bars: int                 # bars used for sigma_ann

    @property
    def mid(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        return self.last


def _realized_vol(closes: list[float]) -> tuple[Optional[float], int]:
    """
    Annualized realized vol from a series of 1-minute closes (oldest-first).

    Returns (sigma_ann, n_bars_used). sigma_ann is None when fewer than
    VOL_MIN_BARS usable returns exist (pre-reg §3.2 missing-data rule).
    """
    series = [c for c in closes if c and c > 0]
    if len(series) < VOL_MIN_BARS:
        return None, len(series)
    rets = [math.log(series[i + 1] / series[i]) for i in range(len(series) - 1)]
    if len(rets) < VOL_MIN_BARS - 1:
        return None, len(series)
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return _ANNUALIZE * math.sqrt(var), len(series)


# --- Binance (primary; geo-restricted in practice) -------------------------

def _binance_quote(underlying: str,
                    session: requests.Session) -> Optional[SpotQuote]:
    """
    Fetch a Binance spot quote + trailing-60m vol. Returns None on ANY error
    (including the expected HTTP 451 geo-block) so the caller can fall back
    to Coinbase. A None return is normal, not exceptional.
    """
    symbol = BINANCE_SYMBOL.get(underlying)
    if symbol is None:
        return None
    try:
        rt = session.get(
            f"{BINANCE_BASE}/api/v3/ticker/bookTicker",
            params={"symbol": symbol}, timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        if rt.status_code in (429, 451, 418):
            log.debug("Binance %s bookTicker HTTP %d (geo/rate) -> fallback",
                      symbol, rt.status_code)
            return None
        rt.raise_for_status()
        tk = rt.json()
        bid = float(tk["bidPrice"])
        ask = float(tk["askPrice"])

        rk = session.get(
            f"{BINANCE_BASE}/api/v3/klines",
            params={"symbol": symbol, "interval": "1m",
                    "limit": VOL_WINDOW_BARS},
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
        )
        closes: list[float] = []
        if rk.status_code == 200:
            # kline row: [openTime, open, high, low, close, volume, ...]
            closes = [float(row[4]) for row in rk.json()
                      if isinstance(row, list) and len(row) > 4]
        sigma, n_bars = _realized_vol(closes)
        return SpotQuote(
            underlying=underlying, source="binance",
            bid=bid, ask=ask, last=(bid + ask) / 2.0,
            exchange_ts_ms=int(tk["time"]) if "time" in tk else None,
            sigma_ann=sigma, n_vol_bars=n_bars,
        )
    except Exception as ex:
        log.debug("Binance %s error -> fallback: %s", symbol, ex)
        return None


# --- Coinbase (fallback; primary-in-practice from a US IP) -----------------

def _coinbase_quote(underlying: str,
                    session: requests.Session) -> Optional[SpotQuote]:
    """Fetch a Coinbase Exchange spot quote + trailing-60m vol."""
    product = COINBASE_PRODUCT.get(underlying)
    if product is None:
        return None
    try:
        rt = session.get(
            f"{COINBASE_BASE}/products/{product}/ticker",
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
        )
        rt.raise_for_status()
        tk = rt.json()
        bid = float(tk["bid"]) if tk.get("bid") is not None else None
        ask = float(tk["ask"]) if tk.get("ask") is not None else None
        last = float(tk["price"]) if tk.get("price") is not None else None
        ex_ts_ms = _coinbase_ts_ms(tk.get("time"))

        # 1-minute candles for the trailing-60m vol. Coinbase candle row:
        # [time, low, high, open, close, volume]; returned newest-first, so
        # we reverse to oldest-first for the close-to-close log returns.
        rc = session.get(
            f"{COINBASE_BASE}/products/{product}/candles",
            params={"granularity": 60}, timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        closes: list[float] = []
        if rc.status_code == 200:
            rows = rc.json()
            if isinstance(rows, list):
                rows_sorted = sorted(
                    (r for r in rows if isinstance(r, list) and len(r) > 4),
                    key=lambda r: r[0],
                )
                closes = [float(r[4]) for r in rows_sorted[-VOL_WINDOW_BARS:]]
        sigma, n_bars = _realized_vol(closes)
        return SpotQuote(
            underlying=underlying, source="coinbase",
            bid=bid, ask=ask, last=last,
            exchange_ts_ms=ex_ts_ms,
            sigma_ann=sigma, n_vol_bars=n_bars,
        )
    except Exception as ex:
        log.warning("Coinbase %s error: %s", product, ex)
        return None


def _coinbase_ts_ms(raw) -> Optional[int]:
    """Parse a Coinbase ISO-8601 ticker timestamp into unix milliseconds."""
    if not raw:
        return None
    from datetime import datetime, timezone
    txt = str(raw).replace("Z", "+00:00")
    # Coinbase returns nanosecond precision; datetime accepts <=6 frac digits.
    if "." in txt and "+" in txt:
        head, rest = txt.split(".", 1)
        frac, tz = rest.split("+", 1)
        txt = f"{head}.{frac[:6]}+{tz}"
    try:
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


# --- public entry point ----------------------------------------------------

def get_spot(underlying: str,
             session: Optional[requests.Session] = None) -> Optional[SpotQuote]:
    """
    Get a spot reference for one underlying: Binance primary, Coinbase
    fallback (pre-reg §2.3). Returns None only if BOTH venues fail.
    """
    s = session or requests.Session()
    quote = _binance_quote(underlying, s)
    if quote is not None:
        return quote
    return _coinbase_quote(underlying, s)


def get_all_spots(session: Optional[requests.Session] = None
                   ) -> dict[str, SpotQuote]:
    """
    Fetch spot references for all four in-scope underlyings.

    Returns a {underlying: SpotQuote} map; underlyings for which both venues
    failed are absent. A small inter-request pause keeps the Coinbase public
    rate budget (10 req/s) comfortable across the 4 underlyings x 2 calls.
    """
    s = session or requests.Session()
    out: dict[str, SpotQuote] = {}
    for sym in BINANCE_SYMBOL:
        q = get_spot(sym, s)
        if q is not None:
            out[sym] = q
        time.sleep(0.15)
    return out
