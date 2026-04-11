# Phase 2 Findings (Tracks 2A, 2B, 2C, 2E)

**Date**: 2026-04-11
**Scripts**: `notebooks/07_classifier_fix.py`, `notebooks/08_price_history.py`,
             `notebooks/09_trader_deep_dive.py`, `scripts/snapshot_markets.py`

---

## Track 2A — Classifier fix (slug-prefix based)

The initial attempt pulled Gamma `category` field for all 66k markets: the field is
100% null on our data. The `events` endpoint returns proper `tags: ['Sports','NBA',...]`
but required an API call per event (~44k calls).

**Final approach**: event_slug prefix rules. Polymarket event slugs are machine-
generated with clean prefixes: `nba-`, `epl-`, `btc-`, `atp-`, `j1100-`, `cfb-`, etc.
302 unique prefixes cover all 44,867 unique event slugs. Rule-based classifier
with zero API calls.

### Results

| Broad category | Markets | % |
|---|---|---|
| crypto | 35,099 | 53.1% |
| sports | 22,167 | 33.5% |
| esports | 4,545 | 6.9% |
| misc | 4,096 | 6.2% |
| news | 120 | 0.2% |
| games | 73 | 0.1% |
| politics | 14 | <0.1% |

### Reclassification of the 17 strict survivors

Applying the corrected classifier + `trades_per_day > 500` HFT filter:

**Drop from strict pool (HFT bots)**:
- apucimama (894 t/d, 46% esports)
- Lilybaeum (4,992 t/d)
- Sharky6999 (593 t/d, 76% crypto)
- 0xc2494b32d6… (7,842 t/d, 97% crypto)

**Drop to "other-category specialist"**:
- fengdubiying (81% esports, mostly League of Legends / CS2)
- 033033033 (56% misc / 17% news / 17% crypto — near-certainty arbitrage)

**Remain in sports pool (11 traders)**:
- 0x006cc834Cc… (91% sports, multi-sport discretionary — **top priority**)
- primm (100% sports, NCAA football focus)
- gatorr (85% sports, NBA heavy)
- ilovecircle (55% sports — also F1, poker, MLB)
- SecondWindCapital (68% sports, 60% tennis grand slam — underdog specialist)
- Blessed-Sunshine (97% sports, 95% NBA)
- PeterDeboerCancerPatient (39% sports — also 53% esports)
- HedgeMaster88 (86% sports — UCL + NBA + MLS + UEL)
- Supah9ga (72% sports — NBA + EPL + UCL)
- piston777 (66% sports NBA + **33% esports LoL**)
- Dragonite35 (55% sports, Ligue 1 / EFL Championship)

### Key revelations from the reclassifier

1. **piston777 is BOTH NBA and LoL scalper**. 40,548 NBA trades + 20,517 LoL trades.
   Their deep-favorite strategy isn't NBA-specific, it's a cross-category pattern.
2. **PeterDeboerCancerPatient is primarily esports** (25,567 LoL + 6,466 CS2 trades).
   We wrongly thought they were a multi-sport generalist.
3. **033033033 is not a sports trader at all** — they trade across misc / news / crypto
   doing near-certainty arbitrage. Their 100% position WR confirmed.
4. **ilovecircle plays poker on Polymarket**: 1,927 poker markets. Not all their edge
   is in sports.

## Track 2B — Price history (priority 4 traders)

Discovered two non-obvious CLOB API behaviors:

1. `interval=max` returns **empty** for closed markets — only works on live markets.
   Our Step 1 spike happened to test on a live market so we missed this.
2. `startTs`/`endTs` **caps the window at ~15 days**. Using `startTs=0, endTs=now`
   returns HTTP 400 `"'startTs' and 'endTs' interval is too long"`.

**Solution**: per-market windows `[first_trade - 1d, resolve_ts + 1d]`, which are
naturally < 15 days for fast-settling markets.

Result: **2,589 of 2,637 tokens successfully pulled (98.2%)**, 9,010,534 price points
stored in `strict_price_history`. 18 min wall clock at ~2.4 req/s (CLOB throttles
prices-history more aggressively than Gamma / Data APIs).

## Track 2C — Forward snapshotting

Script tested locally. Found:
- 50,708 total open markets from Gamma
- 8,966 resolve within 72h AND meet liquidity filter (vol_24h > $500 OR liq > $500)
- At 10 req/s each market needs 2 order-book calls → ~30 min per cycle
- **Too slow for every-10-minute cron**. Options: 30-min cron, 24h horizon instead of
  72h, or sport-category filter. Recommend deploying with a 30-min cron and 48h
  horizon; revisit after first week of data.

Script is ready but not deployed. User deploys to VPS manually.

## Track 2E — Deep dive on 0x006cc834Cc092684F1B566…

The headline finding of the entire project so far.

### Snapshot
- **4,485 trades** → 502 resolved positions (248W / 254L = **49.4% WR**)
- **$4,421,791 realized PnL** (~$8,808 per position)
- 514 days active, 8.7 trades/day, spans Nov 2024 → Mar 2026
- **Top 10 positions = 103% of total PnL** (extreme fat-tail distribution)

### Category breakdown (v2)

| Category | Positions | PnL | PPP | WR |
|---|---|---|---|---|
| **ncaa_basketball** | 134 | $959k | $7.2k | 0.515 |
| **soccer_ucl** | 10 | $927k | **$92.7k** | 0.500 |
| **soccer_epl** | 8 | $651k | **$81.3k** | 0.875 |
| nba | 66 | $515k | $7.8k | 0.485 |
| ncaa_football | 82 | $467k | $5.7k | 0.341 |
| soccer_ligue1 | 3 | $462k | $153.9k | 0.667 |
| nfl | 17 | $200k | $11.8k | 0.588 |
| nhl | 44 | $123k | $2.8k | 0.568 |

**Core insight**: they grind NCAA basketball for volume ($959k over 134 positions),
but the **wealth-moving edge is in top-flight European soccer moneylines**. UCL + EPL
+ Ligue 1 = $2.04M (46% of total PnL) from just **21 positions**.

### Sizing is concentrated on coin-flips

| Entry price bucket | n | median size | mean size | p90 size |
|---|---|---|---|---|
| 0.45-0.55 (toss-up) | 1,348 | $18 | **$12,050** | $18,881 |
| 0.35-0.45 / 0.55-0.65 | 1,117 | $16 | $5,094 | $7,561 |
| 0.20-0.35 / 0.65-0.80 | 347 | $104 | $6,860 | $6,960 |
| 0.05-0.20 / 0.80-0.95 | 165 | $4 | $1,186 | $3,722 |
| 0.00-0.05 / 0.95-1.00 | 7 | $3,761 | $30,924 | $80,834 |

Most of their capital is deployed in the coin-flip band (0.45-0.55). Median trade
is tiny ($18) but mean is $12k — suggesting a mix of small scout bets and huge
conviction bets in the same price range. Only 7 trades in the extreme tails.

### Time of entry — they are a LATE trader

| Hours before resolution | positions | PnL | per position |
|---|---|---|---|
| **<1h** | **248** | **$2,220k** | $8.9k |
| 1-6h | 92 | $850k | $9.2k |
| 6-24h | 47 | $1,213k | **$25.8k** |
| 1-3d | 4 | $1k | $304 |
| 3-7d | 8 | $29k | $3.7k |
| >7d | 102 | $105k | $1.0k |

**54% of their PnL comes from positions entered <1h before resolution.** The 6-24h
bucket is lower count but highest PPP ($25.8k per position). Anything more than a
day out is essentially noise. **The edge is concentrated in the final hour.**

### Slippage — they are LIQUIDITY PROVIDERS

| Stat | Value |
|---|---|
| samples (buys with price history context) | 1,377 |
| median slippage vs market mid | **-50 bps** |
| mean slippage | -227 bps |
| paid **below** market mid | 1,127 / 1,377 (82%) |
| paid above market mid | 244 / 1,377 (18%) |

**This is the most important single finding.** 82% of their fills are below mid —
they are placing passive limit orders and getting hit by takers paying the spread.
They are not chasing trades; they are providing liquidity to other traders who
want immediate execution. This is the opposite of a "sharp" taker strategy.

**Implication for us**: if we try to copy-trade this, we'll enter at a worse price
because we'll be the taker trying to hit their ask. Their edge is partly in the
-227 bps average they collect per fill by being patient.

### Activity schedule

- **Day of week**: Saturday 32.6%, Friday 18.4%, Wed 14.0% (pattern tracks live sports schedules)
- **Hour of day (UTC)**: peak 20:00-23:00 UTC = 3-6 PM ET = **US sports prime time**
- Classification: human trader on US schedule, weekend-focused, live-action times

### Drawdown profile

- Max drawdown: $1,079,770 (took 22 days to recover)
- Best day: +$1,215,365
- Worst day: -$1,079,770 (the drawdown was a single catastrophic day)
- Winning days 68 / 112 (60.7%), losing days 44 / 112 (39.3%)

Roughly equal winning/losing day count but *asymmetric magnitudes* — wins are
bigger than losses on average, sustained through volatility.

### The strategy, as best we can describe it

Based on patterns:
1. **Markets**: top-flight European soccer moneylines (UCL, EPL, Ligue 1) + NCAA basketball totals
2. **Entry**: placed as **passive limit orders** in the final 1-6 hours before resolution
3. **Sizing**: most trades in the coin-flip 0.45-0.55 price band; small scouting
   bets + occasional huge conviction bets
4. **Fill quality**: collects ~50 bps median (~227 bps mean) below market mid by
   being the patient side
5. **Risk**: can lose $1M+ in a single day on unexpected soccer upsets
6. **Volume**: 8.7 trades/day average — **human-pace, not algorithmic**

### 5 testable hypotheses for Phase 3

1. **H1**: If we place passive limit orders below mid on UCL/EPL moneylines in the
   1-6h window before kickoff, we can replicate the -50 bps median fill advantage.
2. **H2**: Their NCAA basketball edge in totals (O/U) is specifically in *under*
   markets (needs verification via direction analysis).
3. **H3**: Their edge degrades if Polymarket liquidity in soccer moneylines
   increases — they're currently extracting rent from a thin book.
4. **H4**: The 6-24h bucket has highest PPP ($25.8k/pos) because it's when conviction
   trades are placed — not volume but size. They decide in the late morning, fill
   over the day.
5. **H5**: The fact that winning and losing top 10 both contain big-name favorites
   (Man City, Juventus, Villarreal, PSG, Liverpool) suggests they bet **on favorites
   in UCL** — a directional strategy, not arbitrage. Testable by computing % of
   favorites-vs-dogs across their UCL book.

## What's next

The most interesting strategies to verify:
1. **0x006cc834Cc's UCL moneyline passive-limit strategy** (this finding)
2. **piston777's NBA+LoL deep-favorite scalping** (median entry 0.99)
3. **SecondWindCapital's tennis grand-slam underdog betting** (median entry 0.14)

And we still need:
- Track 2B-full: price history for the other 13 strict traders + honorable candidates
- Deep dives on piston777 and SecondWindCapital using the same 09_trader_deep_dive.py
- Honorable tier pulls (Track 2B-honorable) for top candidates like SeriouslySirius, bobe2

Phase 3 would begin the external ground-truth joins (sports books closing lines).
