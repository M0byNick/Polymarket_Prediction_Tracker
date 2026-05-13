"""
Polymarket Gamma-API client for weather-temperature events.

Pulls open events tagged `weather`, filters to "highest-temperature-in-X-on-DATE"
and "lowest-temperature-in-X-on-DATE" event slugs, joins to the frozen city
universe in cities.py.

Per pre-reg §1.2 universe is locked: events with city slugs not in
cities.ALL_STATIONS are skipped. Karachi and Ankara are explicitly out.

CLOB price retrieval: for now we read prices off each market's
`outcomePrices` field on Gamma (mid-price, snapshot freshness governed by
Polymarket's own update cadence — usually <60s). A later iteration could
hit CLOB /book directly for top-of-book bid/ask, which is what the existing
snapshot_markets.py does. The pre-reg's filter §4.1 sum-of-bin-prices
sanity check uses these YES prices, so we record what Gamma returns.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

from .cities import Kind, Station, lookup

log = logging.getLogger("weather_arb.polymarket")

GAMMA = "https://gamma-api.polymarket.com"
TIMEOUT = 30
USER_AGENT = "Tracker_Poly weather_forecast_arb research"

# Event slug pattern: "highest-temperature-in-<city>-on-<month>-<day>-<year>"
# or "lowest-temperature-in-<city>-on-...".
_EVENT_SLUG_RE = re.compile(
    r"^(highest|lowest)-temperature-in-([a-z0-9-]+?)-on-([a-z]+)-(\d{1,2})-(\d{4})$"
)

# Inside an event, each market is one temperature bin; market slugs append
# the bin spec, e.g. "...on-may-12-2026-30c", "...on-may-12-2026-31corhigher".
# We parse those to recover the bin range.
_MONTH_TO_NUM = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

# Bin slug suffix forms we recognise. Pre-reg §3.4 needs (T_low, T_high) bounds.
# Forms seen in practice (from 2026-05-12 enumeration):
#   "<number>c"            -> exact integer °C bin centered at N (half-open)
#   "<number>f"            -> exact °F bin
#   "<number>corhigher"    -> open upper bin [N, +inf)
#   "<number>forhigher"    -> open upper bin in °F
#   "<number>corless"      -> open lower bin (-inf, N]
#   "<number>forless"      -> open lower bin in °F
#   "<number>to<number>c"  -> closed range [N1, N2] in °C  (verify forms)
_BIN_RE_INTEGER = re.compile(r"-(\d+)(c|f)$")
_BIN_RE_OR_HIGHER = re.compile(r"-(\d+)(c|f)orhigher$")
_BIN_RE_OR_LESS = re.compile(r"-(\d+)(c|f)orless$")
_BIN_RE_RANGE = re.compile(r"-(\d+)to(\d+)(c|f)$")


@dataclass(frozen=True)
class Bin:
    market_slug: str
    condition_id: str
    t_low: float            # in market's native units (°C or °F)
    t_high: float           # +inf for "or higher", -inf for "or less"
    units: str              # "celsius" or "fahrenheit"
    yes_price: Optional[float]
    no_price: Optional[float]


@dataclass(frozen=True)
class WeatherEvent:
    event_id: str
    event_slug: str
    kind: Kind                 # "highest" or "lowest"
    station: Station           # joined from cities.py
    target_date: str           # 'YYYY-MM-DD' (station-local day per market rules)
    end_date_utc: int          # unix ts of market endDate
    bins: tuple[Bin, ...]


def _parse_event_slug(slug: str) -> Optional[tuple[Kind, str, str]]:
    """Return (kind, city_slug, 'YYYY-MM-DD') or None if slug doesn't match."""
    m = _EVENT_SLUG_RE.match(slug)
    if not m:
        return None
    kind, city, month, day, year = m.groups()
    month_num = _MONTH_TO_NUM.get(month.lower())
    if month_num is None:
        return None
    try:
        target_date = f"{int(year):04d}-{month_num:02d}-{int(day):02d}"
    except ValueError:
        return None
    return kind, city, target_date  # type: ignore[return-value]


def _parse_bin_from_slug(slug: str, event_slug: str) -> Optional[tuple[float, float, str]]:
    """
    Given a market slug and its parent event slug, recover (t_low, t_high, units).
    The market slug is the event slug + a bin-specific suffix.
    """
    suffix = slug[len(event_slug):]
    # Examples of suffix: '-30c', '-31corhigher', '-29corless'
    m = _BIN_RE_OR_HIGHER.search(suffix)
    if m:
        n = float(m.group(1))
        units = "celsius" if m.group(2) == "c" else "fahrenheit"
        return (n, float("inf"), units)
    m = _BIN_RE_OR_LESS.search(suffix)
    if m:
        n = float(m.group(1))
        units = "celsius" if m.group(2) == "c" else "fahrenheit"
        return (float("-inf"), n, units)
    m = _BIN_RE_RANGE.search(suffix)
    if m:
        n1, n2 = float(m.group(1)), float(m.group(2))
        units = "celsius" if m.group(3) == "c" else "fahrenheit"
        return (min(n1, n2), max(n1, n2), units)
    m = _BIN_RE_INTEGER.search(suffix)
    if m:
        n = float(m.group(1))
        units = "celsius" if m.group(2) == "c" else "fahrenheit"
        # Integer-degree bin: treat as half-open [n, n+1) by convention.
        # Pre-reg's Gaussian density treats this analytically; the choice
        # of right-edge inclusivity is symmetric and immaterial under
        # continuous-density assumptions.
        return (n, n + 1.0, units)
    return None


def _coerce_price(raw) -> Optional[float]:
    """Polymarket outcomePrices is a JSON string list, e.g. '["0.42","0.58"]'."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def discover_weather_events(session: Optional[requests.Session] = None) -> list[WeatherEvent]:
    """
    Fetch open weather-tagged events, filter to the frozen universe, return
    parsed WeatherEvents with their bins + current YES/NO prices.
    """
    s = session or requests
    events_out: list[WeatherEvent] = []
    offset = 0
    page = 100
    while True:
        r = s.get(
            f"{GAMMA}/events",
            params={
                "closed": "false",
                "limit": page,
                "offset": offset,
                "tag_slug": "weather",
            },
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break

        for e in batch:
            slug = e.get("slug") or ""
            parsed = _parse_event_slug(slug)
            if not parsed:
                continue
            kind, city, target_date = parsed
            station = lookup(city)
            if station is None:
                # Outside frozen universe — skip (e.g. Ankara, Karachi, or
                # any city not pre-registered).
                continue
            if kind not in station.kinds:
                continue

            end_iso = e.get("endDate") or e.get("endDateIso")
            try:
                end_ts = int(datetime.fromisoformat(
                    str(end_iso).replace("Z", "+00:00")
                ).timestamp())
            except Exception:
                continue

            bins: list[Bin] = []
            for m in e.get("markets") or ():
                m_slug = m.get("slug") or ""
                cid = m.get("conditionId") or ""
                if not (m_slug and cid):
                    continue
                bin_parse = _parse_bin_from_slug(m_slug, slug)
                if not bin_parse:
                    log.debug("bin parse failed: %s (event=%s)", m_slug, slug)
                    continue
                t_low, t_high, units = bin_parse
                # outcomePrices is a JSON-encoded list "[yes, no]"
                import json
                op = m.get("outcomePrices")
                if isinstance(op, str):
                    try:
                        op = json.loads(op)
                    except Exception:
                        op = None
                yes_p = _coerce_price(op[0] if isinstance(op, list) and len(op) > 0 else None)
                no_p = _coerce_price(op[1] if isinstance(op, list) and len(op) > 1 else None)
                bins.append(Bin(
                    market_slug=m_slug, condition_id=cid,
                    t_low=t_low, t_high=t_high, units=units,
                    yes_price=yes_p, no_price=no_p,
                ))

            if not bins:
                continue

            events_out.append(WeatherEvent(
                event_id=str(e.get("id") or ""),
                event_slug=slug,
                kind=kind,
                station=station,
                target_date=target_date,
                end_date_utc=end_ts,
                bins=tuple(bins),
            ))

        if len(batch) < page:
            break
        offset += page

    return events_out
