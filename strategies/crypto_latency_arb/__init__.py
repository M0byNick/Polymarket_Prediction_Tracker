"""
Crypto-binary latency arb collector — Polymarket short-DTE crypto up/down +
fixed-strike binary markets for BTC/ETH/SOL/XRP, paired with a centralized-
exchange spot reference.

OBSERVE-ONLY. This package captures the raw inputs the crypto latency-arb
strategy's holdout will be scored on. It places no orders, touches no wallet,
and risks no capital. It computes the pre-reg §3 Black-Scholes 0DTE binary
model probability as a convenience column, but the core deliverable is the
raw Polymarket top-of-book quotes + CEX spot timestamped to the millisecond.

Pre-registration:
    Prediction_Markets/docs/findings/STRATEGY_CRYPTO_LATENCY_ARB_PREREG_2026-05-14.md
Feasibility audit:
    Prediction_Markets/docs/findings/CRYPTO_LATENCY_ARB_AUDIT_2026-05-14.md

Mirrors the structure of strategies/weather_forecast_arb (pure modules with
no I/O side effects, driven by scripts/crypto_arb_collect.py).
"""
