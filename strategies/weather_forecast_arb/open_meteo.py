"""
Open-Meteo forecast client.

Pre-reg §3.1 URL template:
    https://api.open-meteo.com/v1/forecast
      ?latitude={lat}&longitude={lon}
      &models=icon_seamless,gfs_seamless,ecmwf_ifs025
      &daily=temperature_2m_max,temperature_2m_min
      &hourly=temperature_2m
      &cell_selection=nearest
      &timezone={iana_tz_of_station}
      &forecast_days=2

The response returns per-model daily and hourly series. We extract the
target-day daily max (or min) per model, return the three values for
ensemble combination in strategy.py.

Free-tier limits: 600/min, 5K/hr, 10K/day, 300K/month (no API key required
for research; commercial deployment requires Standard tier per pre-reg §6.2).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("weather_arb.open_meteo")

API_URL = "https://api.open-meteo.com/v1/forecast"
MODELS = ("icon_seamless", "gfs_seamless", "ecmwf_ifs025")
TIMEOUT = 30
USER_AGENT = "Tracker_Poly weather_forecast_arb research (Polymarket)"


@dataclass(frozen=True)
class ModelForecast:
    """A single model's daily max + min for the target station-local day."""
    model: str
    target_date: str        # ISO yyyy-mm-dd in station-local TZ
    temp_max: Optional[float]
    temp_min: Optional[float]


@dataclass(frozen=True)
class StationForecast:
    """Per-station multi-model forecast bundle."""
    icao: str
    lat: float
    lon: float
    tz: str
    target_date: str
    units: str               # "celsius" or "fahrenheit" — what we report in
    models: tuple[ModelForecast, ...]
    api_grid_lat: Optional[float] = None   # actual cell center used by API
    api_grid_lon: Optional[float] = None


def _convert(c_value: Optional[float], to_units: str) -> Optional[float]:
    """Open-Meteo returns Celsius natively. Convert to F if target market is F."""
    if c_value is None:
        return None
    if to_units == "fahrenheit":
        return c_value * 9.0 / 5.0 + 32.0
    return c_value


def fetch(lat: float, lon: float, tz: str, units: str,
          target_date: str, models: tuple[str, ...] = MODELS,
          session: Optional[requests.Session] = None) -> StationForecast:
    """
    Fetch a multi-model forecast for one station + target date.

    Args:
        lat, lon: station coordinates
        tz: station IANA timezone (determines daily aggregation boundary)
        units: 'celsius' or 'fahrenheit' — convert if market resolves in F
        target_date: 'YYYY-MM-DD' in station-local TZ (= the market resolution day)
        models: tuple of Open-Meteo model identifiers
        session: optional requests.Session for connection pooling

    Returns StationForecast with per-model temp_max and temp_min for target_date.
    Raises requests.HTTPError on non-2xx response.
    """
    s = session or requests
    params = {
        "latitude": lat,
        "longitude": lon,
        "models": ",".join(models),
        "daily": "temperature_2m_max,temperature_2m_min",
        "hourly": "temperature_2m",
        "cell_selection": "nearest",
        "timezone": tz,
        "forecast_days": 2,
    }
    r = s.get(API_URL, params=params, timeout=TIMEOUT,
              headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    payload = r.json()

    # Open-Meteo multi-model response uses `daily` with per-variable arrays
    # of length n_models. Time axis is daily.time list.
    daily = payload.get("daily") or {}
    days: list[str] = daily.get("time") or []
    try:
        idx = days.index(target_date)
    except ValueError:
        log.warning("target_date %s not in daily.time=%s for (%.4f,%.4f)",
                    target_date, days, lat, lon)
        # Return empty model list so caller can log a SKIP cleanly
        return StationForecast(icao="", lat=lat, lon=lon, tz=tz,
                               target_date=target_date, units=units,
                               models=tuple(),
                               api_grid_lat=payload.get("latitude"),
                               api_grid_lon=payload.get("longitude"))

    # Open-Meteo's multi-model schema returns variable keyed per-model when
    # multiple models are requested: `temperature_2m_max_icon_seamless`, etc.
    # We try both keying schemes (single-model = no suffix, multi = suffix).
    mfs: list[ModelForecast] = []
    for m in models:
        max_key_m = f"temperature_2m_max_{m}"
        min_key_m = f"temperature_2m_min_{m}"
        max_arr = daily.get(max_key_m)
        min_arr = daily.get(min_key_m)
        # Fallback: single-model query falls back to non-suffixed
        if max_arr is None:
            max_arr = daily.get("temperature_2m_max")
        if min_arr is None:
            min_arr = daily.get("temperature_2m_min")
        tmax_c = max_arr[idx] if max_arr and idx < len(max_arr) else None
        tmin_c = min_arr[idx] if min_arr and idx < len(min_arr) else None
        mfs.append(ModelForecast(
            model=m,
            target_date=target_date,
            temp_max=_convert(tmax_c, units),
            temp_min=_convert(tmin_c, units),
        ))

    return StationForecast(
        icao="",
        lat=lat, lon=lon, tz=tz, target_date=target_date,
        units=units,
        models=tuple(mfs),
        api_grid_lat=payload.get("latitude"),
        api_grid_lon=payload.get("longitude"),
    )
