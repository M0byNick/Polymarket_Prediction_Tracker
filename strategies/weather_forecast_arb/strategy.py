"""
Strategy decision logic — pre-reg §3 (model spec) + §4 (filters).

INPUTS
    - WeatherEvent (parsed from polymarket.py)
    - StationForecast (3 model values from open_meteo.py)
    - now (UTC unix timestamp)

OUTPUT
    Decision: either SKIP with a reason, or TRADE with the chosen bin and
    its model-implied probability, expected EV, and edge magnitude.

This module is PURE — no I/O, no side effects. Easy to unit-test.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

from .open_meteo import StationForecast
from .polymarket import WeatherEvent, Bin

# --- pre-reg §4 + §6.2 constants (LOCKED, do not tune) ---
ENTRY_WINDOW_MIN_HOURS = 18.0       # §5
ENTRY_WINDOW_MAX_HOURS = 30.0       # §5
SUM_PRICES_LOW = 0.85               # §4.1
SUM_PRICES_HIGH = 1.15              # §4.1
PER_BIN_PRICE_LOW = 0.05            # §4.2
PER_BIN_PRICE_HIGH = 0.95           # §4.2
DISAGREEMENT_THRESHOLD_C = 3.0      # §3.3 (auto-converts to 5.4°F for F markets)
SIGMA_FLOOR_C = 0.5                 # §3.4
SIGMA_CEIL_C = 4.0                  # §3.4
EV_FLOOR = 0.02                     # §4.4 (2 cents/dollar net edge)
FEE_PER_LEG = 0.02                  # §6.2
SLIPPAGE_CENTS = 0.01               # §6.2 (1 cent worse fill)


# --- decision schema ---

Outcome = Literal["TRADE", "SKIP"]


@dataclass(frozen=True)
class BinScore:
    """Per-bin model probability + market price + EV computation."""
    bin_index: int
    market_slug: str
    condition_id: str
    t_low: float
    t_high: float
    market_yes_price: Optional[float]
    model_p_yes: float          # pre-reg §3.4 Gaussian
    expected_value: float       # §4.4 EV, net of fees + slippage


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str                              # short label for SKIP, or "ok"
    event_id: str
    event_slug: str
    target_date: str
    station_icao: str
    kind: str
    hours_to_close: float
    sum_bin_prices: Optional[float] = None   # diagnostic
    mu_hat: Optional[float] = None           # ensemble mean (in event units)
    sigma_hat: Optional[float] = None        # ensemble dispersion (clamped, in event units)
    spread_raw: Optional[float] = None       # max-min across models (in event units)
    n_models_used: int = 0
    best_bin: Optional[BinScore] = None      # populated on TRADE
    all_bin_scores: tuple[BinScore, ...] = tuple()


def _units_to_c(value: Optional[float], units: str) -> Optional[float]:
    if value is None:
        return None
    return (value - 32.0) * 5.0 / 9.0 if units == "fahrenheit" else value


def _gaussian_cdf(x: float, mu: float, sigma: float) -> float:
    """Standard normal CDF via erf."""
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _bin_probability(t_low: float, t_high: float, mu: float, sigma: float) -> float:
    """P(model says t in [t_low, t_high]) under Normal(mu, sigma)."""
    if math.isinf(t_low) and t_low < 0:
        lo = 0.0
    else:
        lo = _gaussian_cdf(t_low, mu, sigma)
    if math.isinf(t_high) and t_high > 0:
        hi = 1.0
    else:
        hi = _gaussian_cdf(t_high, mu, sigma)
    return max(0.0, min(1.0, hi - lo))


def _model_values_for_kind(forecast: StationForecast, kind: str) -> list[float]:
    """Extract per-model values (temp_max for highest, temp_min for lowest)."""
    out: list[float] = []
    for mf in forecast.models:
        v = mf.temp_max if kind == "highest" else mf.temp_min
        if v is not None:
            out.append(v)
    return out


def evaluate(event: WeatherEvent, forecast: StationForecast,
             now_utc: int) -> Decision:
    """
    Apply pre-reg §3 + §4 to one event + matching forecast.
    Returns a Decision (TRADE or SKIP) with full diagnostics for logging.
    """
    hours_to_close = (event.end_date_utc - now_utc) / 3600.0
    base = dict(
        event_id=event.event_id, event_slug=event.event_slug,
        target_date=event.target_date, station_icao=event.station.icao,
        kind=event.kind, hours_to_close=hours_to_close,
    )

    # §5: entry window
    if hours_to_close < ENTRY_WINDOW_MIN_HOURS or hours_to_close > ENTRY_WINDOW_MAX_HOURS:
        return Decision(outcome="SKIP", reason="outside_entry_window", **base)

    # §6: must be a single calendar day (target_date present in our parse already)
    if not event.target_date:
        return Decision(outcome="SKIP", reason="missing_target_date", **base)

    # §4.1: sum-of-bin-prices sanity
    yes_prices = [b.yes_price for b in event.bins if b.yes_price is not None]
    if not yes_prices:
        return Decision(outcome="SKIP", reason="no_bin_prices", **base)
    sum_p = sum(yes_prices)
    if not (SUM_PRICES_LOW <= sum_p <= SUM_PRICES_HIGH):
        return Decision(outcome="SKIP", reason="sum_prices_oor",
                        sum_bin_prices=sum_p, **base)

    # §3.1 — extract per-model values for this kind (highest/lowest)
    model_values = _model_values_for_kind(forecast, event.kind)
    if len(model_values) < 3:
        return Decision(outcome="SKIP", reason="missing_models",
                        sum_bin_prices=sum_p, n_models_used=len(model_values),
                        **base)

    # §3.3 disagreement gate — convert spread threshold to event units
    units = forecast.units  # same as event.station.units
    threshold = DISAGREEMENT_THRESHOLD_C if units == "celsius" else DISAGREEMENT_THRESHOLD_C * 9.0 / 5.0
    spread_raw = max(model_values) - min(model_values)
    if spread_raw > threshold:
        return Decision(outcome="SKIP", reason="model_disagreement",
                        sum_bin_prices=sum_p, n_models_used=len(model_values),
                        spread_raw=spread_raw, **base)

    # §3.2 ensemble mean
    mu = sum(model_values) / len(model_values)

    # §3.4 sigma: inter-model dispersion (sample std), clamped in Celsius scale
    mean_for_var = mu
    sigma_raw = math.sqrt(
        sum((v - mean_for_var) ** 2 for v in model_values) / len(model_values)
    )
    # convert clamp bounds from C to event units
    sigma_floor = SIGMA_FLOOR_C if units == "celsius" else SIGMA_FLOOR_C * 9.0 / 5.0
    sigma_ceil = SIGMA_CEIL_C if units == "celsius" else SIGMA_CEIL_C * 9.0 / 5.0
    sigma = max(sigma_floor, min(sigma_ceil, sigma_raw))

    # §3.4 per-bin probability + §4.4 EV
    scores: list[BinScore] = []
    for i, b in enumerate(event.bins):
        # Bins in event units
        p_model = _bin_probability(b.t_low, b.t_high, mu, sigma)
        if b.yes_price is None:
            scores.append(BinScore(
                bin_index=i, market_slug=b.market_slug, condition_id=b.condition_id,
                t_low=b.t_low, t_high=b.t_high, market_yes_price=None,
                model_p_yes=p_model, expected_value=float("-inf"),
            ))
            continue
        # §4.2 per-bin floor/ceiling — tradeable only if price in band.
        # We still record probability for diagnostics, but bin is ineligible.
        # EV per $1 stake on YES side, net of FEE + slippage:
        #   EV = p_model * (1 - market_price - slippage - fee) - (1 - p_model) * (market_price + slippage)
        # We charge ONE leg of fee on entry; the second leg (settlement) is zero
        # since YES contracts settle at $1 with no taker fee. Conservative.
        if PER_BIN_PRICE_LOW <= b.yes_price <= PER_BIN_PRICE_HIGH:
            entry_cost = b.yes_price + SLIPPAGE_CENTS
            ev = p_model * (1.0 - entry_cost - FEE_PER_LEG) - (1.0 - p_model) * entry_cost
        else:
            ev = float("-inf")
        scores.append(BinScore(
            bin_index=i, market_slug=b.market_slug, condition_id=b.condition_id,
            t_low=b.t_low, t_high=b.t_high, market_yes_price=b.yes_price,
            model_p_yes=p_model, expected_value=ev,
        ))

    # §4.4 best bin
    best = max(scores, key=lambda s: s.expected_value)
    base_with_diag = dict(
        base,
        sum_bin_prices=sum_p, n_models_used=len(model_values),
        mu_hat=mu, sigma_hat=sigma, spread_raw=spread_raw,
        all_bin_scores=tuple(scores),
    )
    if best.expected_value < EV_FLOOR:
        return Decision(outcome="SKIP", reason="ev_below_floor",
                        best_bin=best, **base_with_diag)

    return Decision(outcome="TRADE", reason="ok", best_bin=best, **base_with_diag)
