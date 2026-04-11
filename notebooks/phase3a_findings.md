# Phase 3a — Resolved mysteries and discoveries

**Date**: 2026-04-11
**Scripts**: `notebooks/10_piston777_mystery.py`, manual ESPN lookups

Two major corrections to Phase 2E characterizations after diagnostic work.

---

## Correction 1: 0x006cc834Cc is a FADE-FAVORITE directional bettor, not a liquidity provider

Phase 2E concluded "0x006cc834Cc is a passive liquidity provider on Euro soccer
moneylines". That conclusion was wrong — it was based on seeing that 82% of
their fills were below market mid, without checking WHICH SIDE they were buying.

Verifying against ESPN match results for their top winning positions revealed
the actual strategy:

### The 4 biggest wins (UCL / EPL / Ligue 1, 2026-01-20 to 2026-03-04)

| Trade | Result | How they won |
|---|---|---|
| Villarreal vs Ajax (2026-01-20) | 1-2, Ajax won | Bet NO on "Will Villarreal win" → +$730k |
| Sporting CP vs PSG (2026-01-20) | 2-1, Sporting won | Bet NO on "Will PSG win" → +$714k |
| Man City (2026-01-04) | Lost (verified via bet resolution) | Bet NO on "Will Man City win" → +$536k |
| Liverpool (2026-01-01) | Lost/drew | Bet NO on "Will Liverpool win" → +$532k |

All four top winners were "Will [big-name favorite] win?" markets where they
**bet NO** and the favorite failed to win. Four NO bets × ~65-70% market-implied
probability = entry price ~0.27-0.35 per NO share, resolving at $1.00 =
~3-4x return per position.

### Full YES vs NO PnL breakdown (sports only)

| Sub-category | YES PnL | NO PnL | Net |
|---|---|---|---|
| **sports_soccer_ucl** | **-$703** | **+$927,268** | +$926k |
| **sports_soccer_epl** | **-$624,909** | **+$1,275,509** | +$650k |
| **sports_soccer_ligue1** | -$1 | **+$461,683** | +$462k |
| **sports_nba** | -$124,473 | **+$639,374** | +$515k |
| sports_ncaa_football | +$256,601 | +$210,239 | +$467k |
| sports_nhl | +$248,210 | -$125,542 | +$123k |
| sports_ncaa_basketball | **+$1,025,485** | -$65,994 | +$959k |
| sports_nfl | +$78,847 | +$121,369 | +$200k |
| sports_mlb | +$30,388 | +$2,358 | +$33k |

**$3.44M (78%) of their $4.42M PnL comes from NO bets on sports markets.**
YES contributes $966k (22%) largely from NCAA basketball only.

### Two distinct sub-strategies

1. **Pro sports moneylines → fade the favorite** (UCL, EPL, Ligue 1, NBA).
   The market overprices popular teams (Liverpool, Man City, PSG, Villarreal,
   Lakers, Celtics). Betting NO captures the overlay.
   - $3.3M of PnL from ~75 positions
2. **NCAA basketball → bet YES** (likely on totals/spreads, not moneyline).
   Different mechanism, not yet characterized.
   - $1.0M of PnL from 61 positions

### Why the previous analysis misread this

1. The 82%-below-mid slippage statistic captures *fill quality*, which is
   consistent with either limit-order market-making OR limit-order directional
   betting. Both get their fills by waiting for takers.
2. The 49.4% position WR is consistent with either (a) a market-maker who
   doesn't care which side wins OR (b) a directional bettor who wins ~1/3 of
   the time on 3x payouts.
3. We didn't cross-reference the outcome_index × side × payout until Phase 3a.

### Implications for replicating this strategy

The directional interpretation is **much more actionable** than the liquidity-
provider interpretation:

- **Rule**: On Polymarket UCL, EPL, Ligue 1, NBA "Will X win?" markets where X
  is a clear favorite (implied prob > 0.60), buy NO at the current market offer.
  Size ~3% of bankroll per position. Let it ride to resolution.
- **Expected edge**: The $3.3M from ~75 sports-moneyline NO positions over
  514 days implies ~$44k per position / ~15 positions per month. If each
  position is ~$100k capital risk, that's ~3-4% gross edge per position.
- **Required infra**: a way to identify which markets have clear favorites
  (Polymarket implied prob > 0.60) and a way to post NO buys.
- **Risk**: huge drawdown when favorites all win (max DD $1.08M). Needs
  patience and bankroll management.
- **Testable**: YES. We already have the exact outcomes for 75+ positions
  in their history to validate the rule retrospectively, and we can forward-
  test on any upcoming UCL/EPL match.

---

## Correction 2: piston777's math mystery resolved — they are a trend-following active trader, not a deep-favorite scalper

Phase 2E showed 79% position WR with median entry 0.99. Basic EV at those
numbers is -0.20 per dollar, yet they have +$486k. `notebooks/10_piston777_mystery.py`
investigated.

### Key findings

1. **Position WR is actually 83.8%** (the earlier 79% was miscomputed —
   "pushes" were being counted as losses in `09_trader_deep_dive.py`). 
2. **Sizing asymmetry is dramatic**: winners average $35,036 of buy USDC vs
   losers' $8,809 — **4.0x asymmetry**.
3. **They actively trade (SELL) during the position**. BUY trades = 75.8%,
   SELL trades = 24.2%. SELL prices (p50 = 0.880) are below BUY prices
   (p50 = 0.990), but positions often end up profitable because they're
   buying into strength, taking some profits early, and holding the rest.
4. **They trade both YES and NO sides roughly equally** (49.8% YES / 50.2% NO
   by trade count). The 0.99 median entry applies to BOTH sides — meaning
   they're buying deep favorites *and* deep underdogs at 0.99/0.995/0.999.

### What they're actually doing

Looking at specific winning positions:
- **Nuggets vs Warriors (+$43k)**: Bought 162k shares at avg 0.619, sold 51k
  at avg 0.645, net position held to resolution at payout 1.0. They were buying
  into a market that was moving UP (avg fill price rose during the position)
  and locked in partial profits.
- **Spurs vs Thunder (+$23k)**: Bought at avg 0.812 but sold nothing (no
  exit), rode it to 1.0 = 23% return on $100k.

**They are an active trend-following trader** who buys into Polymarket price
moves, takes partial profits on strength, and holds winning positions to
resolution. The 0.99 median BUY price reflects the many small buys they place
*during* a favorable move as the market climbs toward certainty, not a single
entry at 0.99.

This is much more interesting than "deep favorite scalper" but also MUCH
harder to replicate because it requires:
1. Real-time market-move detection
2. Variable sizing based on conviction
3. Active exit management mid-position

**Portability verdict**: hard to replicate as a pure rules-based strategy.
Probably not worth pursuing as a copy-trade target. Possibly interesting as
a feature-extraction study on "how Polymarket sports moneyline prices evolve"
but not actionable as a signal.

---

## Impact on Phase 3 priorities

**Updated priority order**:

1. **0x006cc834Cc's fade-favorite NO-side strategy** — now actually replicable
   and intellectually interesting. This is the clearest edge we've found.
2. **SecondWindCapital's news-event strategy** — interesting but not replicable.
3. **piston777's trend-following** — not worth replicating.
4. **NCAA basketball YES strategy** (also 0x006cc834Cc) — secondary interest,
   needs separate characterization to see if it's favorites/underdogs/totals/spreads.

### Phase 3e model-build scope (revised)

Build a strategy that implements:
- **"Buy NO on UCL/EPL/Ligue 1/NBA markets where `Will [team] win?` implied
  probability > 0.60"**
- Backtest against the 75+ historical positions in our DB
- Cross-validate against ESPN match outcomes (which we now know work)
- Size each position to match 0x006cc834Cc's per-position distribution
- Optionally augment with sharp book comparison (the-odds-api or ESPN DraftKings
  embedded odds) to filter for markets where Polymarket is farthest from fair

This is the minimum viable trading model and can be built in a few hours.
