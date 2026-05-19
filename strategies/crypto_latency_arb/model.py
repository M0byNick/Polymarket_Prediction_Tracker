"""
Black-Scholes 0DTE binary fair-value model (pre-reg §3).

This module is a CONVENIENCE layer. The collector's core job is capturing the
raw Polymarket quotes + CEX spot; this module additionally computes the
pre-reg's model-implied YES probability per snapshot so the holdout-scoring
notebook has the model column pre-populated. It changes no pre-registered
parameter and places no trades — it is pure arithmetic over already-captured
data.

MODEL (pre-reg §3.1, frozen)
    For a "X above $STRIKE at HH:MM" market:
        d2 = (ln(S / X) - 0.5 * sigma^2 * tau) / (sigma * sqrt(tau))
        P_model_above = Phi(d2)
    where
        S     = CEX spot mid at snapshot time
        X     = strike
        tau   = (resolve_ts - snapshot_ts) / (365 * 86400)   [years]
        sigma = trailing-60m annualized realized vol (pre-reg §3.2)
        r     = 0  (carry term ignored at the 15-60 min horizon, pre-reg §3.1)
    Phi is the standard-normal CDF, computed via math.erf (no SciPy needed).

    P_model is NOT clipped (pre-reg §3.3): the digital fair value is allowed
    to saturate to 0 or 1 when |d2| is large.

RELATIVE ("Up or Down") MARKETS
    `updown_*` and `up-or-down` markets resolve on close-vs-OPEN, not against
    a fixed strike. The open price is the spot at the window START, which is
    not knowable at a mid-window snapshot from this collector alone. For
    those markets the model column is left unpopulated with the note
    `relative_market_no_strike`; the holdout notebook reconstructs the open
    from the captured `cex_spot` tape (the first snapshot at/after the
    window start) when scoring. The raw book + spot capture is unaffected.

DEGENERATE CASES (pre-reg §3.3)
    - tau <= 120 sec  -> skip (resolution-window cutoff); note 'tau_too_small'
    - |S - X| within 0.05% of S -> skip (at-the-money, no signal);
      note 'at_the_money'
    - sigma missing / non-positive -> skip; note 'no_sigma'
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# pre-reg §3.3 degenerate-case constants (LOCKED).
TAU_MIN_SECONDS = 120.0
ATM_REL_BAND = 0.0005          # |S - X| / S below this -> at-the-money skip
SECONDS_PER_YEAR = 365.0 * 86400.0


@dataclass(frozen=True)
class ModelResult:
    """Black-Scholes 0DTE binary model output for one (market, snapshot)."""
    p_model_yes: Optional[float]    # None when a degenerate case skipped it
    tau_years: Optional[float]
    note: str                       # 'ok' | 'tau_too_small' | 'at_the_money'
                                     #   | 'no_sigma' | 'relative_market_no_strike'
                                     #   | 'bad_inputs'


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via the error function (matches weather collector)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_binary_above(spot: float, strike: float, sigma_ann: float,
                    tau_years: float) -> float:
    """
    P(spot_at_resolution > strike) under the Black-Scholes 0DTE digital
    formula with r = 0 (pre-reg §3.1). Not clipped.
    """
    vol_sqrt_tau = sigma_ann * math.sqrt(tau_years)
    d2 = (math.log(spot / strike) - 0.5 * sigma_ann ** 2 * tau_years) / vol_sqrt_tau
    return _norm_cdf(d2)


def evaluate(*, market_kind: str, spot: Optional[float],
             strike: Optional[float], sigma_ann: Optional[float],
             snapshot_ts: float, resolve_ts: int) -> ModelResult:
    """
    Compute the pre-reg §3 model-implied YES probability for one snapshot.

    Args:
        market_kind: 'strike' (above $X) or 'relative' (up vs open)
        spot:        CEX spot mid at the snapshot
        strike:      parsed strike (required for 'strike' markets)
        sigma_ann:   trailing-60m annualized realized vol
        snapshot_ts: collector snapshot time, unix seconds (float ok)
        resolve_ts:  market resolution time, unix seconds

    Returns a ModelResult; on any degenerate / missing-input case the
    probability is None and the note explains why (the collector still logs
    the row so the gap is auditable).
    """
    tau_seconds = resolve_ts - snapshot_ts
    tau_years = tau_seconds / SECONDS_PER_YEAR if tau_seconds > 0 else None

    # Relative markets have no fixed strike knowable mid-window.
    if market_kind != "strike":
        return ModelResult(p_model_yes=None, tau_years=tau_years,
                           note="relative_market_no_strike")

    if spot is None or strike is None or spot <= 0 or strike <= 0:
        return ModelResult(p_model_yes=None, tau_years=tau_years,
                           note="bad_inputs")

    if tau_seconds <= TAU_MIN_SECONDS:
        return ModelResult(p_model_yes=None, tau_years=tau_years,
                           note="tau_too_small")

    if abs(spot - strike) / spot < ATM_REL_BAND:
        return ModelResult(p_model_yes=None, tau_years=tau_years,
                           note="at_the_money")

    if sigma_ann is None or sigma_ann <= 0:
        return ModelResult(p_model_yes=None, tau_years=tau_years,
                           note="no_sigma")

    p = bs_binary_above(spot, strike, sigma_ann, tau_years)
    return ModelResult(p_model_yes=p, tau_years=tau_years, note="ok")
