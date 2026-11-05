"""
Frozen city -> resolution-station -> coordinates -> IANA timezone table.

This table is LOCKED per pre-registration §2.1
(STRATEGY_WEATHER_FORECAST_ARB_PREREG_2026-05-12.md). Post-hoc additions or
removals are a Bonferroni-cost trial. Two pre-reg exclusions are baked in:

  - Ankara — Polymarket rules-text URL truncated; ICAO unverifiable.
  - Karachi — Polymarket prose says "Masroor Airbase" but URL embeds /OPKC
    (Jinnah Intl); real Masroor ICAO is OPMR. Attribution conflict.

ICAO is captured from each market's Wunderground or NOAA URL in the rules
text (verified 2026-05-12). Coordinates are the airport reference point per
openflights / Wikipedia airport infobox; Hong Kong uses the HKO HQ at Tsim
Sha Tsui because the market resolves to the Observatory feed, not an airport.

IANA timezone determines the calendar-day boundary the resolution authority
uses (e.g., Wunderground "for all times on this day" — local-station day).
We pass it to Open-Meteo's `timezone=` parameter so daily aggregates align.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Kind = Literal["highest", "lowest"]
ResolutionSource = Literal["wunderground", "noaa", "hko"]


@dataclass(frozen=True)
class Station:
    city: str               # Polymarket slug fragment, e.g. "hong-kong"
    display_name: str       # human-readable
    station_name: str       # named per market rules text
    icao: str               # captured from URL; "HKO" for the Observatory
    lat: float
    lon: float
    tz: str                 # IANA timezone
    units: Literal["celsius", "fahrenheit"]
    source: ResolutionSource
    kinds: tuple[Kind, ...] = ("highest",)


# Highest-temperature universe (Wunderground-resolved). 45 cities.
_WG_HIGHEST: list[Station] = [
    Station("amsterdam",     "Amsterdam",     "Amsterdam Airport Schiphol",            "EHAM", 52.3086,   4.7639,   "Europe/Amsterdam", "celsius",    "wunderground"),
    Station("atlanta",       "Atlanta",       "Hartsfield-Jackson Intl",               "KATL", 33.6367, -84.4281,   "America/New_York", "fahrenheit", "wunderground"),
    Station("austin",        "Austin",        "Austin-Bergstrom Intl",                 "KAUS", 30.1944, -97.6699,   "America/Chicago",  "fahrenheit", "wunderground"),
    Station("beijing",       "Beijing",       "Beijing Capital Intl",                  "ZBAA", 40.0801, 116.5846,   "Asia/Shanghai",    "celsius",    "wunderground"),
    Station("buenos-aires",  "Buenos Aires",  "Ministro Pistarini Intl (Ezeiza)",      "SAEZ", -34.8222, -58.5358,  "America/Argentina/Buenos_Aires", "celsius", "wunderground"),
    Station("busan",         "Busan",         "Gimhae Intl",                           "RKPK", 35.1795, 128.9381,   "Asia/Seoul",       "celsius",    "wunderground"),
    Station("cape-town",     "Cape Town",     "Cape Town Intl",                        "FACT", -33.9648, 18.6017,   "Africa/Johannesburg", "celsius", "wunderground"),
    Station("chengdu",       "Chengdu",       "Chengdu Shuangliu Intl",                "ZUUU", 30.5785, 103.9472,   "Asia/Shanghai",    "celsius",    "wunderground"),
    Station("chicago",       "Chicago",       "Chicago O'Hare Intl",                   "KORD", 41.9786, -87.9048,   "America/Chicago",  "fahrenheit", "wunderground"),
    Station("chongqing",     "Chongqing",     "Chongqing Jiangbei Intl",               "ZUCK", 29.7193, 106.6417,   "Asia/Shanghai",    "celsius",    "wunderground"),
    Station("dallas",        "Dallas",        "Dallas Love Field",                     "KDAL", 32.8471, -96.8517,   "America/Chicago",  "fahrenheit", "wunderground"),
    Station("denver",        "Denver",        "Buckley Space Force Base",              "KBKF", 39.7017, -104.7517,  "America/Denver",   "fahrenheit", "wunderground"),
    Station("guangzhou",     "Guangzhou",     "Guangzhou Baiyun Intl",                 "ZGGG", 23.3924, 113.2988,   "Asia/Shanghai",    "celsius",    "wunderground"),
    Station("helsinki",      "Helsinki",      "Helsinki-Vantaa",                       "EFHK", 60.3172,  24.9633,   "Europe/Helsinki",  "celsius",    "wunderground"),
    Station("houston",       "Houston",       "William P. Hobby",                      "KHOU", 29.6454, -95.2789,   "America/Chicago",  "fahrenheit", "wunderground"),
    Station("jakarta",       "Jakarta",       "Halim Perdanakusuma Intl",              "WIHH", -6.2664, 106.8907,   "Asia/Jakarta",     "celsius",    "wunderground"),
    Station("jeddah",        "Jeddah",        "King Abdulaziz Intl",                   "OEJN", 21.6701,  39.1525,   "Asia/Riyadh",      "celsius",    "wunderground"),
    Station("kuala-lumpur",  "Kuala Lumpur",  "Kuala Lumpur Intl",                     "WMKK",  2.7456, 101.7099,   "Asia/Kuala_Lumpur","celsius",    "wunderground"),
    Station("lagos",         "Lagos",         "Murtala Muhammad Intl",                 "DNMM",  6.5774,   3.3211,   "Africa/Lagos",     "celsius",    "wunderground"),
    Station("london",        "London",        "London City Airport",                   "EGLC", 51.5053,   0.0553,   "Europe/London",    "celsius",    "wunderground", ("highest", "lowest")),
    Station("los-angeles",   "Los Angeles",   "Los Angeles Intl",                      "KLAX", 33.9425, -118.4081,  "America/Los_Angeles", "fahrenheit", "wunderground"),
    Station("lucknow",       "Lucknow",       "Chaudhary Charan Singh Intl",           "VILK", 26.7606,  80.8893,   "Asia/Kolkata",     "celsius",    "wunderground"),
    Station("madrid",        "Madrid",        "Adolfo Suárez Madrid-Barajas",          "LEMD", 40.4719,  -3.5626,   "Europe/Madrid",    "celsius",    "wunderground"),
    Station("manila",        "Manila",        "Ninoy Aquino Intl",                     "RPLL", 14.5086, 121.0194,   "Asia/Manila",      "celsius",    "wunderground"),
    Station("mexico-city",   "Mexico City",   "Benito Juárez Intl",                    "MMMX", 19.4361, -99.0719,   "America/Mexico_City", "celsius", "wunderground"),
    Station("miami",         "Miami",         "Miami Intl",                            "KMIA", 25.7959, -80.2870,   "America/New_York", "fahrenheit", "wunderground", ("highest", "lowest")),
    Station("milan",         "Milan",         "Malpensa Intl",                         "LIMC", 45.6306,   8.7281,   "Europe/Rome",      "celsius",    "wunderground"),
    Station("munich",        "Munich",        "Munich Airport",                        "EDDM", 48.3538,  11.7861,   "Europe/Berlin",    "celsius",    "wunderground"),
    Station("nyc",           "New York City", "LaGuardia",                             "KLGA", 40.7772, -73.8726,   "America/New_York", "fahrenheit", "wunderground", ("highest", "lowest")),
    Station("panama-city",   "Panama City",   "Marcos A. Gelabert Intl",               "MPMG",  8.9737, -79.5556,   "America/Panama",   "celsius",    "wunderground"),
    Station("paris",         "Paris",         "Paris-Le Bourget",                      "LFPB", 48.9694,   2.4414,   "Europe/Paris",     "celsius",    "wunderground", ("highest", "lowest")),
    Station("qingdao",       "Qingdao",       "Qingdao Jiaodong Intl",                 "ZSQD", 36.3661, 120.0883,   "Asia/Shanghai",    "celsius",    "wunderground"),
    Station("san-francisco", "San Francisco", "San Francisco Intl",                    "KSFO", 37.6213, -122.3790,  "America/Los_Angeles", "fahrenheit", "wunderground"),
    Station("sao-paulo",     "São Paulo",     "São Paulo-Guarulhos Intl",              "SBGR", -23.4356, -46.4731,  "America/Sao_Paulo", "celsius",   "wunderground"),
    Station("seattle",       "Seattle",       "Seattle-Tacoma Intl",                   "KSEA", 47.4502, -122.3088,  "America/Los_Angeles", "fahrenheit", "wunderground"),
    Station("seoul",         "Seoul",         "Incheon Intl",                          "RKSI", 37.4691, 126.4505,   "Asia/Seoul",       "celsius",    "wunderground", ("highest", "lowest")),
    Station("shanghai",      "Shanghai",      "Shanghai Pudong Intl",                  "ZSPD", 31.1443, 121.8083,   "Asia/Shanghai",    "celsius",    "wunderground", ("highest", "lowest")),
    Station("shenzhen",      "Shenzhen",      "Shenzhen Bao'an Intl",                  "ZGSZ", 22.6393, 113.8108,   "Asia/Shanghai",    "celsius",    "wunderground"),
    Station("singapore",     "Singapore",     "Singapore Changi",                      "WSSS",  1.3501, 103.9940,   "Asia/Singapore",   "celsius",    "wunderground"),
    Station("taipei",        "Taipei",        "Taipei Songshan",                       "RCSS", 25.0697, 121.5519,   "Asia/Taipei",      "celsius",    "wunderground"),
    Station("tokyo",         "Tokyo",         "Tokyo Haneda",                          "RJTT", 35.5494, 139.7798,   "Asia/Tokyo",       "celsius",    "wunderground", ("highest", "lowest")),
    Station("toronto",       "Toronto",       "Toronto Pearson Intl",                  "CYYZ", 43.6772, -79.6306,   "America/Toronto",  "celsius",    "wunderground"),
    Station("warsaw",        "Warsaw",        "Warsaw Chopin",                         "EPWA", 52.1657,  20.9671,   "Europe/Warsaw",    "celsius",    "wunderground"),
    Station("wellington",    "Wellington",    "Wellington Intl",                       "NZWN", -41.3272, 174.8050,  "Pacific/Auckland", "celsius",    "wunderground"),
    Station("wuhan",         "Wuhan",         "Wuhan Tianhe Intl",                     "ZHHH", 30.7838, 114.2081,   "Asia/Shanghai",    "celsius",    "wunderground"),
]

# NOAA-resolved (3 cities)
_NOAA: list[Station] = [
    Station("istanbul",      "Istanbul",      "Istanbul Airport",                      "LTFM", 41.2625,  28.7411,   "Europe/Istanbul",  "celsius",    "noaa"),
    Station("moscow",        "Moscow",        "Vnukovo Intl",                          "UUWW", 55.5915,  37.2615,   "Europe/Moscow",    "celsius",    "noaa"),
    Station("tel-aviv",      "Tel Aviv",      "Ben Gurion Intl",                       "LLBG", 32.0114,  34.8867,   "Asia/Jerusalem",   "celsius",    "noaa"),
]

# Hong Kong Observatory (special-case: HQ at Tsim Sha Tsui, not an airport)
_HKO: list[Station] = [
    Station("hong-kong",     "Hong Kong",     "Hong Kong Observatory HQ",              "HKO",  22.3022, 114.1742,   "Asia/Hong_Kong",   "celsius",    "hko", ("highest", "lowest")),
]

ALL_STATIONS: tuple[Station, ...] = tuple(_WG_HIGHEST + _NOAA + _HKO)

# Indexes for fast lookup
_BY_SLUG: dict[str, Station] = {s.city: s for s in ALL_STATIONS}


def lookup(slug: str) -> Optional[Station]:
    """Return the Station for a Polymarket city-slug fragment, or None if unknown."""
    return _BY_SLUG.get(slug)


def in_universe(slug: str, kind: Kind) -> bool:
    """True if (slug, kind) is in the frozen pre-reg universe."""
    s = _BY_SLUG.get(slug)
    return bool(s and kind in s.kinds)


# Cardinality sanity check (matches pre-reg §2.2)
_HIGHEST_COUNT = sum(1 for s in ALL_STATIONS if "highest" in s.kinds)
_LOWEST_COUNT = sum(1 for s in ALL_STATIONS if "lowest" in s.kinds)
assert _HIGHEST_COUNT == 49, f"highest universe drift: {_HIGHEST_COUNT} != 49"
assert _LOWEST_COUNT == 8, f"lowest universe drift: {_LOWEST_COUNT} != 8"
