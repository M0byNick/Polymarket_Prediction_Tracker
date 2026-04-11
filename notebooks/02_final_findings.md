# Step 2 Final Findings

**Date**: 2026-04-11
**Script**: `notebooks/02_characterization.py`
**Summary**: `data/polymarket_step2_final_summary.json`
**Runtime**: 52 min 25 s (3,145 s), 15,177 API calls, 0 rate-limits, 1 transient error

## The leaderboard cap is the ceiling

Polymarket's `lb-api.polymarket.com/{profit,volume}` endpoint is **hard-capped
at 50 entries per call** regardless of the `limit` param, and the `offset`
parameter is silently ignored (100% overlap between offset=0 and offset=50).
This was not documented anywhere and cost us a full run to discover.

The only way to expand the universe is to query multiple (kind × window)
combinations. All 8 combinations (profit × {1d,7d,30d,all} + volume ×
same) yield **207 unique traders** after dedupe, which is our effective
universe ceiling.

Source-count distribution of the 207 wallets:

| Appears in # leaderboards | Wallets |
|---|---|
| 1 | 115 |
| 2 | 43 |
| 3 | 26 |
| 4 | 10 |
| 5 | 4 |
| 6 | 5 |
| 7 | 1 |
| 8 | 3 |

**115 of the 207 wallets (55%) appear in only one of the 8 leaderboards** —
i.e., they are completely hidden if you only query the obvious profit_30d
or volume_30d windows. The marginal leaderboards (1d and all) matter.

## Filter funnel

| Stage | Count | % of universe |
|---|---|---|
| Universe (8 leaderboards deduped) | 207 | 100.0 |
| Tier A passed (≥50 recent trades) | 207 | 100.0 |
| Has any resolved PnL data | 206 | 99.5 |
| ≥500 total trades | 201 | 97.1 |
| ≥500 fast-settling trades | 145 | 70.0 |
| Has a strict-counted category (≥250 trades in category, profitable) | 77 | 37.2 |
| Has an honorable-counted category (≥150 trades in category) | 120 | 58.0 |
| **Passes strict** (≥500 fast-settling AND counted cat AND bootstrap p<0.05) | **17** | **8.2** |
| Passes honorable (≥300 fast-settling AND honorable-counted cat) | 119 | 57.5 |

## Universe distributions

| Metric | p25 | p50 | p75 | p90 | max | mean |
|---|---|---|---|---|---|---|
| total_trades | 4,452 | 30,442 | 50,000 | 50,000 | 50,000 | 27,877 |
| fast_settling_trades | 316 | 2,495 | 12,546 | 34,112 | 50,000 | 9,616 |
| resolved_trades | 2,896 | 12,091 | 45,128 | 49,937 | 50,000 | 21,295 |
| unique_markets | 58 | 364 | 1,225 | 2,657 | 11,024 | 978 |
| days_trading | 10.2 | 32.8 | 104.5 | 320.7 | 1,077.8 | 98.5 |
| total_realized_pnl | $64 | $47,799 | $898,982 | $3,100,669 | $22,027,290 | $864,495 |
| pnl_per_trade | $0.06 | $6.50 | $73.11 | $712.87 | $4,629.90 | $228.46 |
| win_rate | 0.432 | 0.527 | 0.704 | 0.989 | 1.000 | 0.552 |

**Headline**: 156 / 206 (75.7%) are profitable on resolved markets.
Total realized PnL across the universe: **$178.9M**.

## Category trade share

| Category | All trades | % all | Fast-settling trades | % fast |
|---|---|---|---|---|
| other (politics/news/events) | 3,054,014 | 52.9% | 818,331 | 41.1% |
| **sports** | 2,578,445 | 44.7% | **1,119,701** | **56.3%** |
| crypto | 127,964 | 2.2% | 45,698 | 2.3% |
| weather | 10,200 | 0.2% | 6,806 | 0.3% |

**Sports dominates the fast-settling space** — 56% of fast-settling trades.
Weather exists but is tiny (0.3% of fast-settling); retain in scope since
one strict survivor in the prior run was a weather specialist.

## Source comparison (leaderboard → strict yield)

| Source | n | strict | honorable | strict yield |
|---|---|---|---|---|
| **profit_1d** | 50 | **6** | 35 | 12.0% |
| profit_7d | 50 | 5 | 39 | 10.0% |
| profit_30d | 50 | 4 | 39 | 8.0% |
| **profit_all** | 50 | **6** | 30 | 12.0% |
| volume_1d | 50 | 1 | 32 | 2.0% |
| volume_7d | 50 | 1 | 23 | 2.0% |
| volume_30d | 50 | 0 | 28 | 0.0% |
| volume_all | 50 | 1 | 28 | 2.0% |

**Correction to Step 1**: the earlier dismissal of `profit_all` as "dormant
2024 election punters" was based on looking at top 5 only. The top-50 view
shows profit_all produces **6 strict survivors**, tied with profit_1d for
the highest strict yield. Top 5 are dormant; ranks 6–50 include real
ongoing sports specialists. Keep profit_all as a seed.

**Volume leaderboards remain weak**: 3 strict survivors total across all
four volume windows (vs 21 across profit). Volume is dominated by
market-making / cross-book arb that fails category profitability.

## The 17 strict survivors

Three distinct edge profiles are visible:

### Profile A — High-WR favorite specialists
Win often (WR ≥ 0.83), presumably betting heavy favorites at small edges.
Large position sizing probably concentrates any losses into a few very bad
bets that bring the realized PnL below perfect.

| Name | PnL | WR | fast | p | notes |
|---|---|---|---|---|---|
| fengdubiying | $3,132,786 | 0.830 | 3,008 | 0.001 | stellar WR, high PnL |
| piston777 | $337,189 | 0.911 | 15,760 | 0.003 | high volume + high WR |
| 033033033 | $194,270 | 1.000 | 1,406 | 0.000 | 100% WR — verify computation |
| Sharky6999 | $44,503 | 0.989 | 23,471 | 0.000 | dual category (sports+crypto) |

### Profile B — Coin-flip volume traders
Near-50% WR but enormous volume and positive per-trade expectancy through
pricing edge.

| Name | PnL | WR | fast | p | notes |
|---|---|---|---|---|---|
| PeterDeboerCancerPatient | $1,069,961 | 0.499 | 33,542 | 0.014 | 33k fast-settling, ~coin-flip |
| 0xc2494b32d6… | $30,224 | 0.517 | 48,941 | 0.002 | 49k fast-settling, tiny edge |
| apucimama | $238,431 | 0.501 | 9,023 | 0.029 | 9k trades, even WR |
| Lilybaeum | $179,705 | 0.483 | 12,708 | 0.033 | slight underdog tilt |

### Profile C — Underdog specialists
Sub-50% WR but profitable via high-payoff rare wins. Classic dog bettor.

| Name | PnL | WR | fast | p | notes |
|---|---|---|---|---|---|
| SecondWindCapital | $2,035,553 | 0.300 | 953 | 0.002 | 30% WR, $2M PnL — extreme dog |
| Supah9ga | $904,429 | 0.444 | 10,967 | 0.018 | milder underdog |

### Profile X — Mid-range (mixed signal)
WR 0.50–0.70, PnL varying. Most generic of the three — probably most
interpretable (least exotic edge sources).

| Name | PnL | WR | fast | p | notes |
|---|---|---|---|---|---|
| 0x006cc834Cc…F1B566 | $4,552,193 | 0.555 | 3,265 | 0.024 | top PnL of the whole set, anonymous wallet |
| primm | $2,670,530 | 0.527 | 1,843 | 0.005 | high PnL on relatively small sample |
| gatorr | $2,268,112 | 0.540 | 23,288 | 0.008 | volume + edge |
| ilovecircle | $2,222,774 | 0.604 | 4,420 | 0.004 | cleanest profile |
| Blessed-Sunshine | $1,201,444 | 0.613 | 847 | 0.031 | smallest sample, borderline |
| HedgeMaster88 | $929,090 | 0.570 | 10,695 | 0.025 | |
| Dragonite35 | $273,560 | 0.530 | 677 | 0.046 | small sample, borderline p-value |

## Cost of the 50k-trade cap

The cap is binding for ~35% of the universe (p75=50,000). Direct casualty:
**kch123**, the dry run's top strict survivor ($11.67M PnL, 54,862
fast-settling trades over full history), dropped to **honorable-only** in
the final run ($2.64M PnL, 19,547 fast-settling trades over the most
recent 50k pulled). This means roughly 77% of kch123's edge lives in
trades older than their most recent 50k, and they are a better sports
bettor historically than the final run reflects.

Similar effect likely applies to other HFT extremes. For Phase 1 deep-dive
we should pull full history (no cap) for the 17 strict survivors.

## Data completeness check

- 99.9% metadata hit rate (83,866 / 83,910 unique condition_ids)
- 0 rate limits across 15,177 requests (effective ~4.8 req/s due to pagination latency)
- 1 transient error out of 207 Tier B pulls
- Pipeline is healthy

## Known / possible issues to verify in Phase 1

1. **033033033's 100% WR** — nearly impossible in genuine sports betting.
   Either (a) their strategy only takes extreme-favorite bets where the
   outcome is near-certain, (b) losses are unrealized / open positions,
   or (c) PnL computation has an edge case bug for some market structure.
   Verify by pulling their raw trade stream and cross-checking.

2. **SecondWindCapital at 30% WR, $2M PnL** — real but unusual. Likely
   concentrated on high-payoff outcomes (underdogs ≥ +300). Verify by
   looking at entry prices: should cluster below 0.30.

3. **Weather vs. politics classification** — the "other" category (41% of
   fast-settling trades) includes both political news markets and misc
   entertainment. Breaking it down would change the denominator of sports
   dominance.

4. **Anonymous wallets in strict tier** — 2 of 17 have no pseudonym set
   (0x006cc834Cc…, 0xc2494b32d6…). Not a problem, just worth noting.

## Decisions and next steps

- Strict-tier shortlist for Phase 1: **17 traders** (see table above)
- Honorable-tier shortlist for Phase 1 feature discovery only: **119 traders**
- Step 2 definitively complete. No more characterization runs needed
  unless we find a specific reason.
- Phase 1 entry point: pull **full history** (no cap) for the 17 strict
  survivors, compute sub-category breakdowns (NBA/NFL/MLB/soccer/etc.),
  and classify edge profile empirically.
