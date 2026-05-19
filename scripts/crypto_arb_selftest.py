#!/usr/bin/env python3
"""
Crypto-binary latency arb collector — pure-module self-test.

Offline assertions for the parsing + model logic in
strategies/crypto_latency_arb. No network, no DB writes. Run with:

    python3 scripts/crypto_arb_selftest.py

Exits 0 if every check passes, 1 otherwise. This is a smoke test, not a
full pytest suite — it mirrors the minimal-dependency style of the repo
(the .venv carries only `requests`).
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.crypto_latency_arb import cex_spot, model, polymarket

_failures: list[str] = []


def check(label: str, cond: bool) -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        _failures.append(label)


def test_updown_classification() -> None:
    print("polymarket: updown slug classification")
    future = int(time.time()) + 3600
    past = int(time.time()) - 3600

    gm = {"slug": f"btc-updown-5m-{future}", "conditionId": "0xabc",
          "clobTokenIds": '["111", "222"]', "question": "BTC up?",
          "description": "resolution source ... Chainlink BTC/USD ..."}
    cm = polymarket._classify_market(gm["slug"], gm, int(time.time()))
    check("btc-updown-5m parsed", cm is not None)
    check("  underlying=BTC", cm and cm.underlying == "BTC")
    check("  family=updown_5m", cm and cm.family == "updown_5m")
    check("  horizon=15min (5m < 20m bound)", cm and cm.horizon == "15min")
    check("  market_kind=relative", cm and cm.market_kind == "relative")
    check("  resolve_ts from slug suffix", cm and cm.resolve_ts == future)
    check("  resolution_source=Chainlink BTC/USD",
          cm and cm.resolution_source == "Chainlink BTC/USD")

    gm15 = {"slug": f"eth-updown-15m-{future}", "conditionId": "0xdef",
            "clobTokenIds": '["3", "4"]'}
    cm15 = polymarket._classify_market(gm15["slug"], gm15, int(time.time()))
    check("eth-updown-15m horizon=15min", cm15 and cm15.horizon == "15min")
    check("eth-updown-15m family=updown_15m",
          cm15 and cm15.family == "updown_15m")

    # 4h window is outside both pre-reg horizon bounds -> dropped.
    gm4h = {"slug": f"btc-updown-4h-{future}", "conditionId": "0x4h",
            "clobTokenIds": '["5", "6"]'}
    cm4h = polymarket._classify_market(gm4h["slug"], gm4h, int(time.time()))
    check("btc-updown-4h dropped (outside horizon bounds)", cm4h is None)

    # Already-resolved market (slug ts in the past) -> dropped.
    gmpast = {"slug": f"btc-updown-5m-{past}", "conditionId": "0xpast",
              "clobTokenIds": '["7", "8"]'}
    cmpast = polymarket._classify_market(gmpast["slug"], gmpast, int(time.time()))
    check("past-resolution updown dropped", cmpast is None)


def test_above_classification() -> None:
    print("polymarket: above-strike slug classification")
    future_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(time.time() + 3600))
    gm = {"slug": "bitcoin-above-75200-on-may-18-2026-10pm-et",
          "conditionId": "0xstrk", "clobTokenIds": '["9", "10"]',
          "groupItemTitle": "75,200", "endDate": future_iso,
          "question": "Bitcoin above 75,200?",
          "description": "... Binance BTC/USDT ..."}
    cm = polymarket._classify_market(gm["slug"], gm, int(time.time()))
    check("above-strike parsed", cm is not None)
    check("  family=above_hourly", cm and cm.family == "above_hourly")
    check("  market_kind=strike", cm and cm.market_kind == "strike")
    check("  strike=75200.0 (comma stripped)", cm and cm.strike == 75200.0)
    check("  horizon=hourly", cm and cm.horizon == "hourly")
    check("  resolution_source=Binance BTC/USDT",
          cm and cm.resolution_source == "Binance BTC/USDT")

    # Strike fallback parse from the slug when groupItemTitle is absent.
    gm2 = dict(gm)
    gm2.pop("groupItemTitle")
    cm2 = polymarket._classify_market(gm2["slug"], gm2, int(time.time()))
    check("above-strike fallback parse from slug", cm2 and cm2.strike == 75200.0)


def test_uporderdown_hourly() -> None:
    print("polymarket: up-or-down hourly classification")
    future_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(time.time() + 3600))
    gm = {"slug": "bitcoin-up-or-down-may-18-2026-9pm-et",
          "conditionId": "0xhr", "clobTokenIds": '["11", "12"]',
          "endDate": future_iso, "description": "... Binance BTC/USDT ..."}
    cm = polymarket._classify_market(gm["slug"], gm, int(time.time()))
    check("up-or-down parsed", cm is not None)
    check("  family=updown_hourly", cm and cm.family == "updown_hourly")
    check("  horizon=hourly", cm and cm.horizon == "hourly")
    check("  market_kind=relative", cm and cm.market_kind == "relative")


def test_book_parse() -> None:
    print("polymarket: CLOB book top-of-book extraction")
    # bids ascending, asks descending — best bid is max price, best ask min.
    entry = {
        "asset_id": "tok1",
        "bids": [{"price": "0.40", "size": "100"},
                 {"price": "0.49", "size": "55"}],
        "asks": [{"price": "0.60", "size": "200"},
                 {"price": "0.51", "size": "33"}],
    }
    tb = polymarket._parse_book("tok1", entry)
    check("best_bid=0.49 (highest bid)", abs(tb.best_bid - 0.49) < 1e-9)
    check("best_ask=0.51 (lowest ask)", abs(tb.best_ask - 0.51) < 1e-9)
    check("bid_size matches best bid", abs(tb.bid_size - 55) < 1e-9)
    check("ask_size matches best ask", abs(tb.ask_size - 33) < 1e-9)
    check("mid=0.50", abs(tb.mid - 0.50) < 1e-9)

    empty = polymarket._parse_book("tok2", {"bids": [], "asks": []})
    check("empty book -> None bid/ask", empty.best_bid is None
          and empty.best_ask is None)
    check("empty book -> None mid", empty.mid is None)


def test_realized_vol() -> None:
    print("cex_spot: trailing realized-vol estimator")
    sigma, n = cex_spot._realized_vol([100.0] * 61)
    check("flat series -> sigma 0.0", sigma is not None and abs(sigma) < 1e-9)
    check("flat series -> n=61 bars", n == 61)

    sigma2, n2 = cex_spot._realized_vol([100.0] * 10)
    check("too-few bars -> sigma None (pre-reg §3.2)", sigma2 is None)

    # Constant per-minute log return r -> sample std 0 -> sigma 0; use an
    # alternating series to get a non-zero, finite, positive estimate.
    series = [100.0 * (1.0 + 0.001 * (1 if i % 2 else -1))
              for i in range(61)]
    sigma3, _ = cex_spot._realized_vol(series)
    check("alternating series -> sigma positive & finite",
          sigma3 is not None and sigma3 > 0 and math.isfinite(sigma3))


def test_bs_model() -> None:
    print("model: Black-Scholes 0DTE binary fair value")
    now = time.time()
    resolve = int(now) + 1800  # 30 min out

    # Deep ITM: spot far above strike -> P(above) ~ 1.
    r_itm = model.evaluate(market_kind="strike", spot=80000.0, strike=70000.0,
                           sigma_ann=0.5, snapshot_ts=now, resolve_ts=resolve)
    check("deep-ITM p_model ~ 1", r_itm.p_model_yes is not None
          and r_itm.p_model_yes > 0.99)
    check("deep-ITM note=ok", r_itm.note == "ok")

    # Deep OTM: spot far below strike -> P(above) ~ 0.
    r_otm = model.evaluate(market_kind="strike", spot=60000.0, strike=70000.0,
                           sigma_ann=0.5, snapshot_ts=now, resolve_ts=resolve)
    check("deep-OTM p_model ~ 0", r_otm.p_model_yes is not None
          and r_otm.p_model_yes < 0.01)

    # ATM-ish (1% above strike, modest vol): p_model strictly in (0.5, 1).
    r_atm = model.evaluate(market_kind="strike", spot=70700.0, strike=70000.0,
                           sigma_ann=0.5, snapshot_ts=now, resolve_ts=resolve)
    check("just-ITM p_model in (0.5, 1.0)", r_atm.p_model_yes is not None
          and 0.5 < r_atm.p_model_yes < 1.0)

    # tau cutoff: resolution 60s out -> skipped.
    r_tau = model.evaluate(market_kind="strike", spot=71000.0, strike=70000.0,
                           sigma_ann=0.5, snapshot_ts=now,
                           resolve_ts=int(now) + 60)
    check("tau<=120s -> note=tau_too_small", r_tau.note == "tau_too_small")
    check("tau<=120s -> p_model None", r_tau.p_model_yes is None)

    # At-the-money band: spot within 0.05% of strike -> skipped.
    r_eq = model.evaluate(market_kind="strike", spot=70000.0, strike=70010.0,
                          sigma_ann=0.5, snapshot_ts=now, resolve_ts=resolve)
    check("|S-X|<0.05% -> note=at_the_money", r_eq.note == "at_the_money")

    # Missing sigma -> skipped.
    r_ns = model.evaluate(market_kind="strike", spot=71000.0, strike=70000.0,
                          sigma_ann=None, snapshot_ts=now, resolve_ts=resolve)
    check("sigma None -> note=no_sigma", r_ns.note == "no_sigma")

    # Relative market -> no strike, model column intentionally unpopulated.
    r_rel = model.evaluate(market_kind="relative", spot=71000.0, strike=None,
                           sigma_ann=0.5, snapshot_ts=now, resolve_ts=resolve)
    check("relative market -> note=relative_market_no_strike",
          r_rel.note == "relative_market_no_strike")
    check("relative market -> p_model None", r_rel.p_model_yes is None)

    # Monotonicity: higher strike -> lower P(above). All three strikes are
    # kept clear of the at-the-money band (|S-X|/S >= 0.05%, i.e. >= $35 off a
    # $70k spot) so each yields a populated probability rather than an
    # at_the_money skip, while still spanning a tight near-the-money range.
    ps = [model.evaluate(market_kind="strike", spot=70000.0, strike=k,
                         sigma_ann=0.5, snapshot_ts=now,
                         resolve_ts=resolve).p_model_yes
          for k in (69900.0, 70100.0, 70300.0)]
    check("P(above) strictly decreasing in strike",
          all(a is not None for a in ps)
          and ps[0] > ps[1] > ps[2])


def main() -> int:
    print("=" * 60)
    print("crypto_latency_arb pure-module self-test")
    print("=" * 60)
    test_updown_classification()
    test_above_classification()
    test_uporderdown_hourly()
    test_book_parse()
    test_realized_vol()
    test_bs_model()
    print("=" * 60)
    if _failures:
        print(f"FAILED — {len(_failures)} check(s): {_failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
