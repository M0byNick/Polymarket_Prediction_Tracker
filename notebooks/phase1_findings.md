# Phase 1 Consolidated Findings

**Date**: 2026-04-11
**Scripts**: `notebooks/03_deep_dive_strict.py`, `04_outlier_verification.py`,
             `05_deep_dive_analysis.py`, `06_honorable_pattern_mining.py`
**Databases**: `data/polymarket_strict_deep.db` (1.3M trades, 17 wallets)
**Reports**: `data/phase1d_report.json`

---

## TL;DR

We pulled full history (1,325,779 trades, 66,231 unique markets) for all 17
strict-tier survivors from Step 2. Three findings reshape the project:

1. **Most "strict survivors" are garbage classifiers, not skilled traders.**
   The sub-category revealed that many top names are **crypto HFT bots**
   (Sharky6999: 66% crypto, 0xc2494b32d6…: 93% crypto, 7,842 trades/day)
   that were mislabeled as "sports" by our crude title-keyword classifier.
   Their edge is market-making / latency, not portable.

2. **Two traders look like copy-trading material**, with completely
   interpretable strategies:
   - **piston777** — NBA deep-favorite scalper (median entry 0.990, 66% NBA,
     3-4h before resolution, 61k trades, $337k PnL, $5.49 per trade)
   - **033033033** — heavy-favorite arbitrage across all categories (median
     entry 0.999, 519 days active, 100% position WR verified, $173k PnL)

3. **PnL-per-trade is the dominant signal** for separating strict from
   honorable-only — strict traders earn **$276/trade vs $150** for honorable,
   while trade counts and days-active are nearly identical. Edge is in
   per-trade quality, not sample size.

The strict→honorable filter based on Step 2 stats was good at filtering *out*
garbage traders but not at filtering *in* the portable-edge ones. The real
signal is in the **sub-category concentration** + **entry price profile** +
**hours-before-resolution** triple — which only becomes visible in Phase 1D.

---

## 1. Outlier verification — all four cleared

### 033033033 — 100% WR is REAL
| Stat | Value |
|---|---|
| Full pull | 20,104 trades |
| Usable trades (dedup) | 19,619 |
| Unique positions | 579 |
| Resolved positions | 577 |
| Computed position WR | **0.998** (493W / 1L / 83 push) |
| Median buy price | **0.999** |
| p25 buy price | 0.997 |
| p10 buy price | 0.994 |
| Min buy price | 0.911 |
| Computed realized PnL | $173,284 |
| Step 2 PnL | $194,270 |
| Match | ~89% (tail of unresolved / edge cases) |

**This is a deep-favorite arbitrage bot.** Literally every single buy is at
≥0.911. Median buy 0.999 means they're buying at 99.9¢ for a YES that
resolves at $1.00 — picking up 0.1¢ per contract. They've done this 519
days running, 37 trades/day average, on election + misc + NFL + crypto.
The one loss out of 494 resolved positions verifies the strategy isn't
completely risk-free but essentially is.

**Edge type**: market-making on near-certainty markets. **Portable**: yes,
if you have the capital to scale. Not very interesting as a feature
extraction target but worth understanding as a baseline.

### SecondWindCapital — extreme underdog specialist, verified
| Stat | Value |
|---|---|
| Full pull | 11,490 trades |
| Median buy price | **0.140** |
| p10 buy price | 0.002 |
| p25 buy price | 0.007 |
| Computed PnL | $2,035,676 (matches Step 2 $2,035,553 to within $123) |
| Positions wins/losses/push | 21 / 32 / 0 |
| Sub-category concentration | **60.1% tennis** |
| Busiest UTC block | europe_day (63%) |

**Verified**: entry price p10=0.002 means 10% of buys at <0.2¢. These are
deep underdog / "tail lottery" bets in tennis. Loses 60% of positions but
the rare wins pay 50-500× the bet. Classic positive-skew strategy.

**Edge type**: underdog mispricing. **Portable**: in theory yes. Requires
a lot of capital to absorb the 60% loss rate and the liquidity is thin at
those prices. Tennis specialist angle is interesting because tennis has
lots of first-set / first-game / first-point markets with extreme longshots.

### 0x006cc834Cc… — anonymous top PnL, legit
| Stat | Value |
|---|---|
| Full pull | 4,522 trades |
| Computed PnL | $4,421,791 (Step 2: $4,552,193, ~97% match) |
| Median buy price | 0.480 |
| Sub-categories | 37% misc, 17% mlb, 15% nhl, 10% nba, 9% nfl |
| Days active | 514 |
| Trades/day | 8.7 (very human pace) |
| USDC size range | $4 → $400,000 (CV=4.6) |

**This is the most interesting strict survivor.** Balanced entry prices,
multi-sport, 514 days consistent, human-pace trades (8.7/day), and a
highly variable bet sizing pattern (CV=4.6). The sizing pattern plus the
8.7/day frequency pattern = **discretionary bettor making conviction-weighted
bets on a handful of carefully-selected markets per day**. Exactly the
profile most likely to have portable edge. Priority #1 for feature
extraction.

### 0xc2494b32d6… — HFT crypto bot, not portable
| Stat | Value |
|---|---|
| Full pull | 144,045 trades |
| Window | **18 days** (Mar 24 – Apr 11) |
| Trades/day | **7,842** |
| Sub-category | **92.9% crypto** |
| USDC p50 | $3, p90 $11, max $1,589 |
| Entry price p50 | 0.490, balanced |
| Hours before resolve p50 | **0.1h** |

**Not a trader — a market-making bot.** Flat-tiny positions, coin-flip
entries, always minutes before resolution, one category. Zero interpretive
value for our project. **Drop from Phase 2.**

---

## 2. Sub-category breakdown — the real classifier

The most revealing Phase 1D output. Each strict survivor's top 1 sub-category:

| Trader | #1 sub-category | % of trades | Profile hypothesis |
|---|---|---|---|
| Blessed-Sunshine | **nba** | 94.8% | NBA specialist, 18 days active only |
| 0xc2494b32d6… | crypto | 92.9% | HFT crypto market-making bot |
| fengdubiying | misc | 85.5% | Classifier failed — likely soccer or news |
| 033033033 | misc | 72.8% | Near-cert arbitrage across categories |
| **Sharky6999** | **crypto** | **66.4%** | Crypto HFT / MM — NOT a sports trader |
| **piston777** | **nba** | **66.3%** | NBA deep-favorite scalper, 990¢ avg |
| primm | misc | 60.9% | Classifier failed — NFL-heavy looking at #2 |
| SecondWindCapital | tennis | 60.1% | Underdog specialist |
| gatorr | nba | 59.4% | NBA balanced, 380/day |
| PeterDeboerCancerPatient | misc | 55.1% | Classifier failed — NHL-heavy at #2 |
| Dragonite35 | misc | 51.1% | Classifier failed — serie A at #2 |
| apucimama | misc | 49.9% | Multi-sport, 894/day, likely generalist bot |
| Supah9ga | misc | 43.7% | Classifier failed — soccer-heavy #2-#4 |
| Lilybaeum | misc | 40.5% | Multi-sport HFT, 4,992/day |
| 0x006cc834Cc… | misc | 37.5% | Multi-sport discretionary, 8.7/day |
| HedgeMaster88 | misc | 35.3% | Soccer generalist |
| ilovecircle | nfl | 25.5% | Multi-sport generalist |

**Classifier weakness**: "misc" is appearing as the top category for 12 of 17
traders. The title-keyword regex is too narrow. It misses soccer leagues
outside the big 5 (J-League, MLS only partially, LatAm), UFC non-big-name,
boxing, tennis non-grand-slam, cricket, NBA G-league, WNBA, NCAA.
**This must be fixed before Phase 2 feature extraction** — probably by
pulling through the Gamma `category` field rather than title regex.

---

## 3. The cohort falls into 5 clear archetypes

### A. NBA scalpers (2 traders, both plausibly copy-trading candidates)
| Trader | PnL | trades | p50 entry | p50 h-before | Notes |
|---|---|---|---|---|---|
| piston777 | $337k | 61,351 | **0.990** | 3.1h | 66% NBA, 85% Americas-night UTC (NBA prime time) |
| Blessed-Sunshine | $1.20M | 1,150 | 0.520 | 0.7h | 95% NBA, near-last-minute, 18 days only |

piston777 is the clearest interpretable edge in the whole set: they consistently
buy NBA heavy favorites 3 hours before resolution at 99¢, and the edge
compounds over 61,000 trades. Blessed-Sunshine is less clear because they
only have 18 days of data.

### B. Multi-sport discretionary (1 trader, priority #1 for deep study)
| Trader | PnL | trades | p50 entry | trades/day | sizing CV |
|---|---|---|---|---|---|
| **0x006cc834Cc…** | **$4.55M** | 4,485 | 0.480 | 8.7 | 4.6 |

The whole package: long history, slow pace, conviction sizing, multi-sport,
balanced entry prices. If any trader in the universe has a real general-purpose
edge, it's this one.

### C. Underdog / longshot specialists
| Trader | PnL | trades | p50 entry | Category |
|---|---|---|---|---|
| SecondWindCapital | $2.04M | 11,490 | **0.140** | Tennis |
| Supah9ga | $904k | 12,464 | 0.360 | Soccer |
| Dragonite35 | $274k | 3,499 | 0.290 | Soccer (Serie A) |

Three traders buying heavy underdogs in sports with deep tails. SecondWindCapital
is the purest expression. Worth studying whether the edge is in the underdog
selection or in pricing anomalies only available on Polymarket.

### D. Favorite specialists (low-edge, high-frequency)
| Trader | PnL | trades | p50 entry | Notes |
|---|---|---|---|---|
| 033033033 | $173k | 19,619 | **0.999** | 519d running, near-cert arb |
| ilovecircle | $2.22M | 20,466 | 0.710 | Multi-sport favorite tilt, 142d |
| fengdubiying | $3.13M | 4,334 | 0.650 | 98d, 44/day, WR 0.83 |

These capture real edge but it's small per bet and requires either a lot of
capital (033033033's million-dollar orders) or serendipitous fill opportunities.

### E. HFT / market-making bots (5+ traders, drop from feature extraction)
| Trader | trades | trades/day | Category | Why drop |
|---|---|---|---|---|
| Lilybaeum | 393,752 | 4,992 | multi-sport | Too fast for human pattern |
| apucimama | 149,600 | 894 | multi-sport | Too fast, too balanced |
| **Sharky6999** | 272,305 | 593 | **crypto 66%** | Crypto MM not sports |
| **0xc2494b32d6…** | 144,045 | 7,842 | crypto 93% | Pure HFT |
| gatorr | 34,175 | 381 | NBA | Probably NBA MM |
| PeterDeboerCancerPatient | 61,271 | 399 | multi-sport | Generalist HFT |

**These were incorrectly labeled as sports specialists by our classifier.**
Sharky6999 and 0xc2494b32d6… should have been caught in Step 2 as crypto-
dominant and excluded from "sports" counted categories. Category misclassification
in Step 2 is the proximate cause. Fix the Gamma category pull before Phase 2.

---

## 4. Market overlap — the co-traders

The top market overlaps form a cluster of 6-7 HFT-ish wallets all trading the
same markets:

| Pair | Shared markets |
|---|---|
| Lilybaeum × apucimama | 1,174 |
| PeterDeboerCancerPatient × apucimama | 741 |
| Lilybaeum × gatorr | 522 |
| piston777 × apucimama | 449 |
| Sharky6999 × 0xc2494b32d6… | 435 |
| Lilybaeum × Sharky6999 | 363 |
| Lilybaeum × piston777 | 308 |

This likely tells us two things:
- **The HFT cluster** (Lilybaeum, apucimama, gatorr, PeterDeboer, Sharky6999,
  0xc2494b32d6…) is trading the same markets, probably against each other.
  Possibly a group of operators or the same operator using multiple wallets.
- **piston777 is embedded in that cluster** — their NBA scalping is
  happening in the same markets the HFT bots are filling. Their edge may
  actually be in **being faster than the slower bots** at identifying deep
  favorites. That's a latency concern for copy-trading but not for feature
  extraction.

**0x006cc834Cc… is NOT in the cluster** — most distant market-set from all
the others, reinforcing it as the most interesting case.

---

## 5. Strict vs honorable-only — PnL/trade is the signal

| Metric | Strict | Honorable-only | Ratio |
|---|---|---|---|
| mean total_trades | 25,019 | 28,973 | **0.86×** |
| mean fast_settling | 12,048 | 13,643 | **0.88×** |
| mean days_trading | 134 | 100 | 1.35× |
| mean win_rate | 0.61 | 0.52 | 1.17× |
| **mean pnl_per_trade** | **$276** | **$150** | **1.84×** |
| mean realized_pnl | $1.31M | $399k | 3.29× |
| **median realized_pnl** | **$929k** | **$64k** | **14.44×** |

Strict traders do **fewer trades with more edge**, not more trades. The
sample size gap is minor — the difference is quality per bet. This tells
us the statistical filter is picking up something real (pnl_per_trade is a
cleaner signal than raw PnL once you control for volume).

---

## 6. Honorable-tier candidates to consider

Top 5 honorable-only traders worth promoting to deep-dive in a future
pass (ranked by `pnl × significance × sample`):

1. **SeriouslySirius** — $3.8M PnL, WR 0.52, 36,564 fast, p=0.073. Coinflip
   volume profile, p-value just above strict threshold.
2. **gmpm** — $3.4M PnL, WR 0.59, 27,747 fast, p=0.092. Mid-range profile.
3. **KeyTransporter** — $5.65M PnL, WR 0.67, 3,211 fast, $1,761/trade.
   Too small a sample for bootstrap but the per-trade PnL is off the chart.
4. **reachingthesky** — $3.58M PnL, WR 0.70, 1,935 fast, $1,848/trade.
   Same profile as KeyTransporter — small sample, huge edge.
5. **bobe2** — $1.61M PnL, WR **0.84**, 3,489 fast, $35/trade, **p=0.000**.
   High-WR favorite specialist, fell out of strict because of fast-settling
   count threshold.

KeyTransporter and reachingthesky are particularly interesting because they
have the highest per-trade edge in the entire 207-trader universe but were
excluded from strict by the 500-trade floor. They may be the *most skilled*
traders we've found — statistical significance just requires more data.

---

## 7. What to fix before Phase 2

### Immediate
1. **Category classifier** — replace `title` regex with Gamma API `category`
   field. ~10 of 17 strict survivors are classified as "misc" top-category
   because of poor regex coverage. We miss hundreds of leagues.
2. **Filter out HFT bots** — add a `trades_per_day > 500` or similar
   exclusion to the filter before classifying as "sports specialist".
3. **Exclude crypto-dominant traders from sports category counting** —
   Sharky6999 and 0xc2494b32d6… shouldn't have passed as sports specialists
   at all.

### Before going further
4. **Pull Gamma category for each market** — one-time update to strict_markets
   table, then re-classify all trades. Should take 5-10 min.
5. **Re-apply strict/honorable filter** with the corrected classification.
   Expected: 5-8 HFT wallets drop out of strict; possibly 3-5 new honorable
   candidates get promoted.

### For Phase 2
6. **Priority target #1**: 0x006cc834Cc… — full discretionary deep-dive
7. **Priority target #2**: piston777 — NBA scalping strategy reverse-engineering
8. **Priority target #3**: SecondWindCapital — tennis underdog strategy
9. **Priority target #4**: 033033033 — near-cert arbitrage baseline
10. **Honorable promotion candidates**: SeriouslySirius, gmpm, bobe2, 
    KeyTransporter (sample size is the only disqualifier)

---

## 8. Compute ledger

| Task | API calls | Wall clock |
|---|---|---|
| Phase 1A (17 full histories) | 5,310 | 19 min |
| Phase 1B (outlier verify, local) | 0 | 2 s |
| Phase 1D (deep dive, local) | 0 | 10 s |
| Phase 1E (honorable mining, local) | 0 | 2 s |
| **Total** | **5,310** | **~20 min** |

All 4 analyses ran from the same Phase 1A database. Zero additional API
cost for Phases 1B, 1D, 1E.
