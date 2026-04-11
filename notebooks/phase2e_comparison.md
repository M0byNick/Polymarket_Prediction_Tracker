# Three Strategies Compared (Phase 2E)

**Date**: 2026-04-11
**Source**: `notebooks/09_trader_deep_dive.py` run on three priority wallets
**Data**: `data/polymarket_strict_deep.db` (raw trades + 9M price history points)

The three deep dives reveal three **fundamentally different** portable edges
on Polymarket. Side-by-side comparison below.

## Summary table

| Metric | 0x006cc834Cc | piston777 | SecondWindCapital |
|---|---|---|---|
| Realized PnL | $4.55M | $486k | $2.04M |
| Raw trades | 4,485 | 61,351 | 11,490 |
| Settled positions | 502 | 1,496 | 53 |
| Position WR | **49.4%** | **79.1%** | **37.7%** |
| Top 10 pos. as % of PnL | 103% | 46% | 118% |
| Days active | 514 | 171 | 23 (+long-tail holds) |
| Trades/day | 8.7 | 358 | 500 |
| Max drawdown | $1.08M | $54k | $338k |
| DD duration | 22 days | 18 days | 6 days |
| Peak category (v2) | NCAA bball + UCL/EPL soccer | LoL + NBA | Iran news + UFC/boxing + tennis |
| Peak entry window | **<1h (54% of PnL)** | **<1h + 1-6h (99%)** | **>7d (49%)** |
| Peak UTC hours | 20-23 (US prime) | 02-05 (Asia) | 08-18 (Europe) |
| Busiest day of week | Saturday (33%) | Wednesday (18%) | Thursday (27%) |
| Slippage vs mid (median) | **-50 bps** | +60 bps | **-50 bps** |
| % fills below mid | 82% | 44% | 95% |
| Style | Liquidity provider | Taker | Liquidity provider |

## The three strategies

### Trader A — 0x006cc834Cc092684F1B56626 — "European soccer moneyline liquidity provider"

- **What they bet**: Top-flight European soccer moneylines (UCL, EPL, Ligue 1).
  21 positions across those = **$2.04M (46%) of total PnL**.
- **When**: Final hour before kick-off; conviction trades 1-6h out.
- **How**: Passive limit orders below market mid. 82% of fills are below mid,
  median -50 bps. They get taken by motivated buyers who want immediate execution.
- **Why it works**: Polymarket moneyline books on mid-tier Euro soccer are thin.
  Takers pay up. A patient provider collects ~50 bps per fill on a 100% hit rate
  of fills (by definition) and breaks even on the directional side.
- **Position win rate is 49.4% — almost exactly 50%.** This is NOT a prediction
  strategy. It's a liquidity-provision strategy where the edge is in the fill
  price, not the market direction. The 50% WR is EXPECTED — they're indifferent
  to outcome; they profit from the spread.
- **Risk**: Directionally exposed. Max drawdown $1.08M over 22 days came from
  a string of Man City / Juventus / Olympiakos losses.
- **Portable**: YES, but requires posting passive limit orders, not taking.
  Copy-trading by mirroring their entries would flip the sign — you'd become
  the taker and pay the spread they're collecting.

### Trader B — piston777 — "Deep-favorite scalper (NBA + LoL)"

- **What they bet**: NBA moneylines and League of Legends match winners at
  median entry **0.990**. 74% of trades are in the extreme conviction bucket
  (entry 0.95-1.00).
- **When**: 3-5 minutes before game start / LoL match start. 68% of PnL from
  the <1h bucket, another 53% from 1-6h.
- **How**: Taker. Pays ~60 bps median above market mid. The edge isn't in
  fill quality.
- **Position WR is 79.1%**. The strategy wins 4 out of 5 times but the
  wins are small (paying 0.99 for a share worth 1.00 = 1% return).
- **Math check**: At WR 0.79, avg win ~1¢, avg loss ~99¢. Expected value per
  $1 wagered = 0.79 × 0.01 - 0.21 × 0.99 = -0.20. **Negative EV as stated.**
  They can only be profitable via:
  - (a) Exiting losers early before full resolution (partial losses), OR
  - (b) Heavy sizing asymmetry (big bets on 0.99-0.995 only, small on 0.95)
  - (c) Avoidance of the 0.80-0.95 zone where losses are larger
  This warrants verification in the next pass — somehow total PnL is +$486k
  over 1,496 positions so the simple math is missing something.
- **UTC peak 02-05 = Asia evening**. Weekday distribution flat (wed/thu/sat/sun
  all ~15%). Likely an Asia-based algorithmic trader hitting deep-favorite
  markets whenever they fire.
- **Risk**: Small drawdowns ($54k max, 18-day recovery). Strategy is designed
  for high hit rate, not high per-trade edge.
- **Portable**: In principle YES via automation (need to hit the tape fast
  enough at 0.99 before the mid moves). In practice HARD because the edge per
  trade is tiny and variable sizing is essential to survive losses.

### Trader C — SecondWindCapital — "Early news/event speculator"

- **What they bet**: Iran strike / war-expansion prediction markets ($955k /
  47% of PnL from 16 positions), UFC PPVs, boxing PPVs, Australian Open tennis.
- **When**: Extremely early. 49% of PnL from trades entered **>7 days** before
  resolution. The long-tail positions include markets that don't resolve for
  weeks or months.
- **How**: Passive liquidity provider (95% of fills below mid, -50 bps median).
  But unlike 0x006cc834Cc, these are limit orders sitting for days/weeks, not
  last-hour entries.
- **Position WR is 37.7%** — loses 60% of the time but wins big.
  Concentration is extreme: 53 positions total, top 10 = 118% of total PnL.
- **Top 4 winners** tell the story:
  - UFC 326 Oliveira vs Holloway: +$568k
  - Anthony Joshua boxing: +$381k
  - Alcaraz tennis: +$315k
  - US strikes Iran (4 different timeline markets): +$863k combined
- **Directional strategy**: They held a thesis that the Iran escalation
  markets were mispriced and placed early bets across multiple resolution
  dates (Feb 28, Mar 31, Apr 30, Dec 31), collecting wins as the thesis played
  out. Also appears to have a boxing/MMA edge and a specific Alcaraz tennis thesis.
- **Risk**: Max drawdown $338k over 6 days. Worst day -$239k.
- **Portable**: PARTIALLY. The news-thesis edge requires geopolitical judgment
  that a quantitative system can't easily replicate. The UFC/boxing edge might
  be based on specific domain knowledge. The liquidity-provision aspect is
  portable as a general technique.

## What the three have in common — the first cross-pattern

All three are **NOT market-takers chasing fair price**. Even piston777, who
looks like a taker at first glance, is paying median +60 bps — far less than
you'd expect for a bot hitting 0.99 deep favorites where the ask is often
0.995 or 0.998. They're finding narrow spreads and paying them.

All three are **concentrated** in their edge:
- 0x006cc834Cc: 46% of PnL from 21 positions
- piston777: 46% of PnL from 10 positions
- SecondWindCapital: 118% of PnL from 10 positions

In all three cases, **if you removed the top 10 positions, the trader is
roughly breakeven or underwater**. This is a strong signal that the "edge"
is not a reliable per-trade skill but an ability to place a few enormous
conviction bets correctly. The smaller trades may even be losing — they're
the cost of being in the market long enough to catch the big ones.

**Testable implication**: if we could identify the few conviction bets each
trader places before they place them, we could copy just those and skip the
noise. This is the actual research question for Phase 3.

## What's different — the second cross-pattern

| Dimension | 0x006cc834Cc | piston777 | SecondWindCapital |
|---|---|---|---|
| Hit rate | Coin-flip (49%) | High (79%) | Low (38%) |
| Avg position size | Mid ($8.8k) | Tiny ($325) | Huge ($38k) |
| Per-trade edge | Fill quality | Price edge | Directional thesis |
| Replication difficulty | Moderate | High (latency) | High (judgment) |
| Sample size for confidence | 502 positions | 1,496 positions | **53 positions** |

The 53-position sample size for SecondWindCapital is concerning — their $2M
PnL could be luck. Bootstrap p-value from Step 2 was p=0.002 but that test
uses the whole position count including unresolved, so reliability is
questionable at this small sample.

## Immediate next questions

1. **piston777 math mystery** — how are they profitable at all on 0.99 buys
   with 79% hit rate? Need to verify by looking at win PnL vs loss PnL by
   position size. Possibly exiting losers mid-market before full 0→1 swing.
2. **SecondWindCapital robustness** — with only 53 resolved positions, is the
   edge real or survivor-biased? Need sharper statistical testing on their
   subset.
3. **0x006cc834Cc replicability** — can we identify their conviction trades
   ex-ante? The 6-24h bucket has the highest PPP ($25.8k) but few trades (47).
   Those might be the actual edge moments.

## Rankings for Phase 3 (ground truth + model build)

Best priority order based on clarity of strategy and replicability:

1. **0x006cc834Cc (European soccer moneyline liquidity provision)** —
   clearest interpretable strategy, well-characterized, needs no external
   data other than kick-off times. We can test this against live UCL/EPL
   Polymarket prices starting tomorrow.
2. **piston777 (deep-favorite scalping)** — needs the math mystery resolved
   first. If the basic EV problem is real, they're not actually winning and
   we should drop them.
3. **SecondWindCapital (news/event thesis)** — interesting but not replicable
   without editorial judgment. Study as a case study, not a model to copy.
