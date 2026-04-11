# Polymarket Trader Portfolio Analysis

Research project to identify Polymarket traders with **portable** edges in
fast-settling markets, reverse-engineer the features driving those edges, and
build our own trading model from them. Emphasizes statistical rigor over
PnL-ranking and fast feedback loops over broad market coverage.

Sibling project: `../Prediction_Markets/` — Kalshi-focused bot with reusable
infra patterns (SQLite WAL, `log.py`, `db.py`, Telegram alerts, paper trading,
Streamlit dashboard). This project reuses those patterns but uses a separate DB
and separate cron.

## Goals

1. Find Polymarket traders whose edge is statistically significant **and**
   portable to our own trading (not latency / insider / news-feed edges).
2. Reverse-engineer the features driving their edge using hypothesis testing
   and revealed-preference classifiers.
3. Build our own signal from those features, validated against sharp-book /
   weather / crypto ground truth.
4. Forward-test on paper, then go live.

## Scope: Fast-Settling Only (≤72h to Resolution)

- **Sports**: NBA, NFL, MLB, NHL, CFB, tennis, etc.
- **Weather**: daily highs, precipitation
- **Short crypto brackets**: minute-to-hour BTC/ETH/SOL price levels

**Excluded**: politics, Fed/CPI, long-horizon markets. These settle too slowly
for efficient learning and rarely have clean verifiable external ground truth.

## Trader Filter

### Strict tier (eligible for signal generation)

- Total trades in fast-settling markets ≥ **500**
- A category X only "counts" toward a trader's resume if:
  - trades in X ≥ **250**, **and**
  - PnL in X > 0
- Trader qualifies if total trades ≥ 500 **and** at least 1 counted category
- **Block bootstrap p<0.05** on total PnL (block size 20, 10,000 resamples)
- **Edge source classified as portable** (see classification below)

### Honorable mentions tier (pattern discovery only)

- Total trades ≥ **300**, counted category ≥ **150**
- Used to expand the pool for feature discovery / hypothesis formation
- **Never used for live signal generation** — research inputs only

## Edge-Source Classification

Before deep-dive analysis each trader is classified by likely edge source.
Non-portable edges are dropped from the strict tier.

| Edge type | Portable? | Heuristic to detect |
|---|---|---|
| Model edge (better probability estimates) | Yes | Enters at prices diverging from sharp-book consensus, wins on avg |
| Cognitive / bias exploit | Yes | Patterns cluster on known biases (favorite-longshot, home bias, recency) |
| Cross-market arb (Poly ↔ Kalshi/sportsbook) | Partial | Entries correlate with Polymarket vs sharp-book gaps |
| Liquidity provision / market making | Partial | Many small bets, spread-capture shape, low per-trade PnL × high count |
| Latency / insider / news feed | **No** | Entries immediately precede public news; burst activity around events |

## Ground-Truth Sources

- **Sports**: Free / scraped for MVP (ESPN scores, oddsportal, the-odds-api free
  tier). Pay for the-odds-api (~$30–100/mo) later if it becomes the bottleneck.
- **Weather**: NWS forecasts + METAR observations — reuse logic from
  `../Prediction_Markets/screeners/weather.py`.
- **Crypto**: Binance / Coinbase minute OHLCV (free, no key needed).

## Phase Plan

- **Step 1** (Phase 0 spike): 10-trader feasibility — validate API pipeline,
  measure rate limits, estimate Step 2 scale, confirm we can reconstruct
  market price at trade entry time.
- **Step 2** (full characterization): top 1,000 traders by volume (union of 30d
  + all-time) — compute distributions + filter funnel. Notebook-driven analysis.
- **Step 0.75** (parallel, start ASAP): forward-snapshotting cron on VPS —
  every 5 min, capture mid-price + book depth for all ≤72h fast-settling
  markets. Data is cheap to start logging and expensive to realize we didn't.
- **Phase 1**: Trader screening (apply strict + honorable tiers + bootstrap).
- **Phase 1.5**: Edge-source classification, drop non-portable.
- **Phase 2**: Deep dive on survivors — hypothesis-driven **and**
  revealed-preference (per-trader classifier) feature extraction.
- **Phase 3**: Ground-truth validation against external sources.
- **Phase 4**: Build + backtest own model; **20% time-split holdout locked
  from day one**.
- **Phase 5**: Paper forward test → live.

## Storage & Research Surface

- SQLite at `data/polymarket_traders.db` (WAL mode, adapt
  `../Prediction_Markets/db.py` patterns).
- Notebooks in `notebooks/` for research and exploratory analysis.
- Production scripts at repo root (adapted from sibling patterns).
- `data/` for DB, raw API dumps, logs.

## Conventions

- **NO AXIOS** in any JS code — security alert. Use native `fetch`.
- Python 3.9; use `from __future__ import annotations` (not `X | None` at runtime).
- Logging via `log.py` — never `print()` for operational output.
- Secrets via `.env` file — never hardcode.
- Paper trade only until out-of-sample validation completes.
- **Overfitting discipline**: hold out the most recent 20% of each trader's
  trades as a locked test set from day one. Never look at it until a feature
  set is committed.
- **Compute planning**: discuss plan + ETA + parallelism strategy before any
  long-running job (per global directive in `~/.claude/CLAUDE.md`).
- Update this CLAUDE.md after each phase milestone with what was learned.

## Infra

- VPS (shared with Prediction_Markets) but separate DB and separate cron.
- Same logging / secrets / SQLite patterns as sibling project.

## Current State (2026-04-11)

- Plan scoped and approved.
- **Step 1 spike complete** — see `notebooks/01_feasibility_findings.md`.
  Three blocking bugs found and fixed; two strategic findings
  (profit/all=dead 2024 punters, volume/all=HFT bots).
- **Step 2 pipeline complete** (`notebooks/02_characterization.py`) and
  **dry-run validated at `limit-per-lb=50`**:
  - 98 unique traders in universe
  - 9 strict-tier + 53 honorable-only survivors
  - Every strict survivor is a sports specialist
  - Weather essentially nonexistent on Polymarket (<0.1% of fast-settling trades)
  - profit_30d and profit_7d produce ~7× more strict survivors than volume leaderboards
  - Top survivor: kch123, $11.67M PnL, 0.594 WR, 54,862 fast-settling sports trades, bootstrap p=0.006
  - **Runtime surprise**: 57 min at `limit=50` (vs my estimate of 2-3 min)
    — top traders have 100k+ lifetime trades; three hit the 150k safety cap
- **Tier B cap added**: `--max-tier-b-pages=20` (10,000 trades/trader)
  prevents HFT extremes from dominating runtime. Still well above the
  500 fast-settling floor for all real candidates.
- **Next**: full Step 2 run at `limit-per-lb=500` with Tier B cap,
  projected ~45–90 min in background.

## Decisions Log

- **2026-04-11** — Scope = fast-settling only, 72h cutoff.
- **2026-04-11** — Approach = feature extraction (not copy-trading) for MVP;
  mirror / ensemble deferred.
- **2026-04-11** — Strict filter = 500 total / 250 per counted category,
  category must be profitable, block bootstrap p<0.05.
- **2026-04-11** — Honorable mentions tier = 300 / 150 for pattern discovery only.
- **2026-04-11** — Edge-source classification required before feature
  extraction; drop non-portable edges.
- **2026-04-11** — Sports ground truth = free/scraped for MVP, pay later if needed.
- **2026-04-11** — Research surface = notebooks in `notebooks/`; production
  scripts at repo root.
- **2026-04-11** — VPS deployment, shares infra with `../Prediction_Markets/`
  but separate DB and separate cron.
- **2026-04-11** (post-spike) — Leaderboard seed = recent windows (7d + 30d),
  both profit and volume, deduped. `window=all` is not useful for our purpose.
- **2026-04-11** (post-spike) — Fast-settling is a per-trade property:
  `resolve_ts - trade_ts ≤ 72h`, computed at entry. Not a per-market property.
- **2026-04-11** (post-spike) — Step 2 uses tiered pulls: headline-only pass on
  all candidates, full pull only on Tier-A survivors.
- **2026-04-11** (post-spike) — Activity API pagination uses `end=<ts-1>`, not
  `offset=` (hard cap at offset=3500). Gamma lookups require
  `condition_ids=<cid>&closed=true` and must be validated by matching the
  returned `conditionId`.
- **2026-04-11** (post-dry-run) — Tier B capped at 20 pages (10k trades) per
  trader. HFT extremes (top volume leaderboard) have 100k+ lifetime trades;
  pulling everything adds runtime without changing characterization. Recent
  10k trades give enough signal for bootstrap + WR + PnL-per-trade stats
  while keeping full-run time manageable.
- **2026-04-11** (post-dry-run) — Weather effectively dropped from Polymarket
  scope (<0.1% of fast-settling trades across 98 top traders). Retain in
  the plan but don't expect any weather-specialist traders in practice.
- **2026-04-11** (post-dry-run) — Profit leaderboards (30d and 7d) are
  ~7× more likely to produce strict survivors than volume leaderboards.
  Volume leaders retained as negative controls but not primary source.

## Reference

- Polymarket Data API: user activity, positions, leaderboard (free, public)
- Polymarket Gamma API: market metadata
- Polymarket CLOB API: order book, price history
- Existing sibling integration: `../Prediction_Markets/polymarket_client.py`
  (read-only, CLOB+Gamma for crypto cross-validation only — does not touch Data API)
