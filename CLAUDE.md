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
  (profit/all top-5 = dormant 2024 punters, volume/all = HFT bots).
- **Step 2 final run complete** — see `notebooks/02_final_findings.md`.
  Ran three times to land on the right parameters:
  1. Dry run at `limit=50`, no Tier B cap — 57 min, 98 traders, 9 strict
  2. Full run at `limit=500`, 10k cap — 7.5 min, 98 traders, 5 strict
     (revealed Polymarket leaderboard **hard-caps at 50 entries**, ignoring
     both `limit>50` and `offset`; cap too aggressive, HFT extremes' edges
     got truncated)
  3. **Final run** with **8 leaderboards** (`profit` × {1d,7d,30d,all} +
     `volume` × same) and 50k cap — **52 min, 207 unique traders,
     17 strict, 119 honorable**
- **Correction to Step 1 finding**: `profit/all` produces 6 strict survivors
  when you look at ranks 6-50 (not just top 5). Keep it as a seed.
- **Three edge profiles** visible in the 17 strict survivors:
  - *High-WR favorite specialists* (WR ≥ 0.83): fengdubiying, piston777,
    Sharky6999, 033033033
  - *Coin-flip volume players* (~0.50 WR, huge sample): PeterDeboerCancerPatient,
    0xc2494b32d6…, apucimama
  - *Underdog specialists* (WR < 0.50, high payoff): SecondWindCapital,
    Supah9ga
- **Weather is thin but alive**: 6,806 fast-settling weather trades across
  207 traders (0.3% of fast-settling). One weather specialist in a prior
  run (ArmageddonRewardsBilly) so keep it in scope.
- **Sports dominates** fast-settling at 56.3% of trades. Crypto 2.3%,
  weather 0.3%, "other" (news/politics/events) 41.1%.
- **Universe-wide**: $178.9M total realized PnL, 75.7% profitable on
  resolved markets, median PnL $48k, p90 $3.1M, max $22M.
- **Phase 1 complete** — see `notebooks/phase1_findings.md`. Pulled
  1,325,779 raw trades (66,231 unique markets) for all 17 strict survivors
  in 19 min (5,310 API calls). Three critical findings:
  1. **Category classifier is broken**. "misc" is the top category for 12
     of 17 strict survivors because the title-keyword regex misses most
     leagues. Worse: Sharky6999 (66% crypto) and 0xc2494b32d6… (93% crypto,
     7,842 trades/day) were wrongly counted as sports specialists.
     **~5-8 strict survivors are actually HFT bots that should fail the
     filter.** Must fix Gamma-category-based classification before Phase 2.
  2. **Outlier verification passed**:
     - 033033033 100% WR is REAL (median buy 0.999, deep-cert arbitrage,
       519 days running)
     - SecondWindCapital $2M PnL REAL (tennis underdog, median buy 0.140,
       p10 0.002, 60% loss rate with positive skew)
     - 0x006cc834Cc… $4.55M is REAL and the **most promising trader in
       the whole set** — multi-sport discretionary, 8.7 trades/day, 514
       days active, highly variable sizing (CV=4.6), balanced entry prices
     - 0xc2494b32d6… is a pure crypto HFT bot — drop from all further work
  3. **Strict traders earn $276/trade vs $150** for honorable-only while
     trade counts are similar (25k vs 29k mean). Edge is per-trade quality.
- **Three clearly interpretable edge strategies identified:**
  - **piston777**: NBA deep-favorite scalper, median entry 0.990,
    3-4h before resolution, 66% NBA, 85% Americas-night UTC,
    61,351 trades, $337k PnL
  - **SecondWindCapital**: tennis underdog specialist, median entry 0.140
  - **0x006cc834Cc…**: multi-sport discretionary, top priority for
    feature extraction (highest PnL, cleanest profile)
- **Phase 2 complete** (Tracks 2A, 2B, 2C, 2E) — see
  `notebooks/phase2_findings.md`:
  - **2A: Classifier fixed** via slug-prefix rules (Gamma `category` field
    is 100% null; `events` endpoint per-call too slow; slugs are
    machine-generated and cleanly classifiable). 302 prefixes cover all
    44,867 unique event slugs, zero API calls.
  - **Reclassification dropped 4 HFT bots from strict** (apucimama,
    Lilybaeum, Sharky6999, 0xc2494b32d6…). Dropped 2 to other-category
    (fengdubiying = esports, 033033033 = near-cert arb). Sports pool
    shrank from 17 to 11.
  - Two surprises: **piston777 is NBA+LoL** (33% of their trades are
    League of Legends, same deep-favorite strategy applied to both).
    **PeterDeboerCancerPatient is mostly esports** (53%), not sports.
  - **2B: Price history pull** working after two bugs fixed:
    `interval=max` silently empty for closed markets; `startTs/endTs`
    capped at ~15 days. Per-market windows work. 9.0M price points
    stored for the 4 priority traders (2,589/2,637 tokens = 98.2%).
  - **2C: Snapshot script tested**, needs deployment. 8,966 markets
    pass liquidity filter at 72h horizon → ~30 min per cycle, needs
    30-min cron or shorter horizon.
  - **2E: 0x006cc834Cc deep dive** — headline finding:
    * 49.4% position WR, top 10 positions = 103% of total PnL
    * $2.04M / 46% of total PnL from just **21 UCL/EPL/Ligue 1
      soccer moneyline positions**
    * 54% of PnL from trades entered <1h before resolution
    * **82% of fills below market mid** (median -50 bps) — they are
      a passive liquidity provider, not a taker
    * Human schedule: US prime-time sports hours (20-23 UTC), weekend-
      heavy
    * Strategy: passive limit orders on top-flight Euro soccer
      moneylines in the final hour, collecting spread from takers
- **Phase 2E deep dives complete** — see
  `notebooks/phase2e_comparison.md`. Three distinct portable strategies:
  * **0x006cc834Cc** = Euro soccer moneyline liquidity provider
    (82% passive fills, 46% of PnL from 21 UCL/EPL/Ligue 1 positions,
    <1h entries). **Clearest portable edge in the whole project.**
  * **piston777** = Deep-favorite scalper across NBA + LoL
    (median entry 0.99, 79% WR, but the basic EV math is suspicious
    at that strike — needs verification). Asia-timezone, ~360 t/day.
  * **SecondWindCapital** = Early news/event speculator (49% of PnL
    from >7-day holds, top wins in Iran strike / UFC / boxing /
    Alcaraz tennis). Only 53 resolved positions total — sample size
    concern. Not replicable without editorial judgment.
- **Cross-pattern**: all three are concentrated bettors. Top 10
  positions = 46-118% of total PnL. Remove the whales and each trader
  is roughly breakeven. Implication for Phase 3: the research question
  is "can we identify the conviction bets ex-ante?"
- **piston777 math mystery** (needs resolving): at 0.99 median entry
  and 79% WR, the basic EV is -0.20 per dollar. Yet total PnL is +$486k.
  Possible explanations: exiting losers before full 0→1 swing, heavy
  sizing asymmetry, or a computation edge case. Verify in Phase 3.
- **Phase 3a major correction** — 0x006cc834Cc is NOT a liquidity provider.
  ESPN lookups on their top winning positions (Villarreal 2026-01-20,
  PSG 2026-01-20, Man City 2026-01-04, Liverpool 2026-01-01) reveal that
  **all four matches were LOST by the favorite**, and 0x006cc834Cc bet
  **NO on "Will [favorite] win?"** in every case. $1.44M + $536k + $532k
  across these 4 positions alone. They are a **fade-favorite directional
  bettor**, not a market-maker. The 82%-below-mid slippage finding is
  coincidental — they happen to get passive fills because they're patient
  waiting for the NO side to come down.
- **Phase 3a piston777 mystery resolved** — position WR is actually 83.8%
  (earlier 79% had a push-counting bug). The +$486k PnL comes from 4x
  sizing asymmetry (winners avg $35k vs losers $8.8k) plus active trend-
  following. They are NOT a deep-favorite scalper — they buy into
  favorable price moves and take partial profits. Much harder strategy
  to replicate. Drop from priority list.
- **Phase 3d** — ESPN API validated as ground truth source. Free,
  public, historical, covers UCL / EPL / La Liga / Ligue 1 / Bundesliga /
  Serie A with DraftKings odds embedded in some responses.
- **Phase 3e** — fade-favorite model built and backtested against
  0x006cc834Cc's actual positions. After separating moneyline from
  spread/total:
  - **24 pure moneyline NO positions in target Euro soccer + NBA**
  - 62.5% WR, **+$2.53M PnL** on $3.75M staked = **67% ROI**
  - YES comparison: 30.4% WR, -$1.45M, -82% ROI
  - **Net edge: 149 percentage points of ROI**
  - **NBA moneyline NO does NOT work** (10 positions, -$134k, -21% ROI)
    — 0x006cc834Cc's NBA edge is in totals and spreads, not moneyline
  - **Euro soccer moneyline NO works spectacularly**:
    * EPL: 5 pos, 100% WR, +$1.28M, **154% ROI**
    * Ligue 1: 1 pos, 100% WR, +$462k, 152% ROI
    * UCL: 7 pos, 57% WR, +$927k, 47% ROI
  - Threshold sweet spot: NO price ≤ 0.35 (implied fav ≥ 65%)
    gives **5 of 5 historical wins, +$2.23M, 2.15x ROI**
- **PRIMARY MODEL RULE** (ready for forward testing):
  *"On Polymarket EPL / UCL / Ligue 1 / La Liga / Serie A 'Will [team] win?'
  moneyline markets, buy NO when offered at ≤ 0.35 (market-implied favorite
  probability ≥ 65%). Hold to resolution."*
  - Historical sample: 13 European soccer positions, 77% WR, +$2.66M,
    104% ROI
  - Caveats: small n, survivorship bias inherent in studying a profitable
    trader. Need forward validation.
- **Phase 3d ESPN validation**: joined 0x006cc834Cc's 20 Euro soccer
  moneyline positions to ESPN match results. 19/20 matched, 12 ✓ consistent
  (PnL direction matches outcome direction), 0 inconsistent.
- **CRITICAL CORRECTION**: The fade-favorite edge is partially about **draws**.
  In Polymarket "Will [team] win?" markets, draws resolve NO. European soccer
  has ~25-30% draw rates. For 0x006cc834Cc's 8 winning NO positions:
  * 4 came from draws (+$1.97M, 184% ROI on $1.07M staked)
  * 4 came from team losses (+$1.65M, 154% ROI on $1.07M staked)
  The edge captures BOTH outcomes.
- **Honorable-tier cross-validation** (Phase 3e expanded):
  Pulled full history for 25 top honorable candidates (1.1M trades),
  found **332 similar NO-side Euro soccer moneyline positions** with
  stake >$100:
  * Aggregate: 48.8% WR, +$4.9M PnL on $23.9M stake = 21% ROI
  * 0x006cc834Cc's 67% ROI is now clearly an outlier vs the cross-
    trader norm. The real edge is closer to 20-25% ROI, not 100%+.
  * Best confirming signal: SeriouslySirius (40 positions, 57.5% WR,
    60% ROI) and kch123 (9 positions, 55.6% WR, 73% ROI).
  * Dispersed: some traders (waterbottle6, HorizonSplendidView) lose
    money on the same strategy — suggesting it's not price-magnitude
    that matters, but trade selection within the high-fav pool.
- **Combined sample: 356 NO-side Euro soccer moneyline positions,
  ~49% WR, ~23% ROI, ~$7.4M PnL**. This is a defensible statistical edge.
- **Refined model rule** (ready for forward paper test):
  *"On Polymarket European soccer 'Will [team] win?' moneyline markets,
  buy NO when market-implied favorite prob ≥ 65%. Expected gross ROI:
  20-30% per position. WR near 49% (coin flip) because edge is in
  skew from draws + upsets."*
- **Phase 3c running overnight**: full price history pull for all 42
  wallets (~13 hours, 117k tokens). Task `bxdio1xdi` in background.
  Will complete by morning.
- **Next session**: (a) deep dives on top honorable traders after
  price history completes, (b) forward paper test of the fade-favorite
  rule on live EPL/UCL markets, (c) sharp-book validation via the-odds-
  api to test whether Polymarket systematically mis-prices favorites.

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
- **2026-04-11** (post-full-run) — Polymarket leaderboard endpoint is
  **hard-capped at 50 entries** regardless of `limit` param, and `offset`
  is silently ignored (100% overlap between offset=0 and offset=50).
  Confirmed empirically. Maximum universe via leaderboards is ~207 unique
  wallets across all 8 (kind × window) combinations.
- **2026-04-11** (post-final-run) — Step 2 final seeds all 8 leaderboard
  combinations (profit and volume × 1d, 7d, 30d, all). 115 of 207 wallets
  appear in only one leaderboard — multi-window seeding is necessary to
  capture them.
- **2026-04-11** (post-final-run) — Correction: the Step 1 dismissal of
  `profit/all` was based on top-5 only; ranks 6-50 include real active
  sports specialists, and profit/all contributed 6 strict survivors to
  the final run (tied with profit/1d for highest strict yield).
- **2026-04-11** (post-final-run) — Tier B cap at 100 pages (50k trades)
  is the chosen balance. 10k was too aggressive (kch123 dropped from
  strict to honorable). 50k keeps most HFT extremes well-characterized
  while holding runtime under 1 hour. Phase 1 deep dive will pull full
  history (no cap) for the 17 strict survivors specifically.
- **2026-04-11** (post-phase-1) — Category classifier based on `title`
  keyword regex is insufficient. Must pull Gamma API `category` field
  directly for each market and re-classify before running any further
  filters. Proximate cause of 5-8 HFT bots being mislabeled as sports
  specialists.
- **2026-04-11** (post-phase-1) — Add `trades_per_day > 500` to the
  strict filter to exclude market-making / HFT bots. Real discretionary
  traders cluster around 1-50 trades/day; the HFT ones run at 400-8000/day.
- **2026-04-11** (post-phase-1) — Per-trade PnL is the dominant signal
  separating strict from honorable traders ($276 vs $150) while trade
  counts and days-active are nearly identical. The strict filter works
  at identifying quality per bet, not sample-size diligence.
- **2026-04-11** (post-phase-1) — Feature-extraction priority order:
  (1) 0x006cc834Cc092684F1B566 — discretionary multi-sport, $4.55M PnL
  (2) piston777 — NBA deep-favorite scalper, most interpretable strategy
  (3) SecondWindCapital — tennis underdog specialist
  (4) 033033033 — deep-certainty arbitrage baseline
  Drop 0xc2494b32d6… and Sharky6999 from any further sports-edge analysis
  (they're crypto HFT, not sports traders).
- **2026-04-11** (post-phase-2-A) — Polymarket Gamma `category` field is
  100% null on our 66k markets. `events` endpoint has the real tags
  (`['Sports','NBA','Games','Basketball']`) but would require ~45k calls.
  **Event slug prefixes are the right signal**: machine-generated,
  consistent, 302 unique prefixes cover all 44k slugs. Zero-API classifier
  works.
- **2026-04-11** (post-phase-2-A) — Strict filter needs HFT exclusion:
  `trades_per_day > 500` drops 4 wallets (apucimama, Lilybaeum,
  Sharky6999, 0xc2494b32d6…) that were mislabeled as sports specialists.
  Sports pool shrank from 17 to 11.
- **2026-04-11** (post-phase-2-B) — Two CLOB `/prices-history` gotchas:
  (1) `interval=max` silently returns empty for closed markets (only
  works on live ones); (2) `startTs`/`endTs` caps window at ~15 days.
  Always use a tight per-market window `[first_trade - 1d, resolve_ts + 1d]`.
  98.2% hit rate with this approach.
- **2026-04-11** (post-phase-2-E) — 0x006cc834Cc strategy characterized:
  passive-limit liquidity-provision on Euro soccer moneylines, 82% of
  fills below market mid, 54% of PnL from last-hour trades, 46% of PnL
  from 21 UCL/EPL/Ligue 1 positions. A human-pace (8.7 trades/day) US-
  hours sports-schedule trader. The strategy is genuinely portable but
  requires patience (placing limit orders and waiting) rather than
  taker speed.
- **2026-04-11** (post-phase-2-C) — Snapshot cron: 72h horizon +
  liquidity filter yields 8,966 markets / ~30 min per cycle. Options for
  deployment: 30-min cron at full scope, or shorter horizon (48h), or
  sport-category filter. Deploy script is in `scripts/snapshot_markets.py`
  but not installed on VPS yet — user deploys manually.

## Reference

- Polymarket Data API: user activity, positions, leaderboard (free, public)
- Polymarket Gamma API: market metadata
- Polymarket CLOB API: order book, price history
- Existing sibling integration: `../Prediction_Markets/polymarket_client.py`
  (read-only, CLOB+Gamma for crypto cross-validation only — does not touch Data API)
