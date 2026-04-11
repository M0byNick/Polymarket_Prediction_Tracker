# Step 1 Feasibility Spike — Findings

**Date**: 2026-04-11
**Script**: `notebooks/01_feasibility.py`
**Raw output**: `/tmp/poly_spike_output.json` (not in repo)
**Sample**: 10 traders (top-5 profit-all + top-5 volume-all, deduped)

## Runtime & rate limits
- 152 requests in 111.8s wall clock, 1.4 req/s effective (intentionally throttled)
- **0 rate-limit (429) responses** — plenty of headroom
- 30 HTTP 400s, all from the offset-pagination bug (see F1), not transport failures

## Endpoints confirmed working

| Endpoint | Purpose | Notes |
|---|---|---|
| `lb-api.polymarket.com/profit` | Top traders by PnL | `window=1d\|7d\|30d\|all`, `limit` param |
| `lb-api.polymarket.com/volume` | Top traders by volume | Same params |
| `data-api.polymarket.com/activity` | User trade history | `limit=500` max; **use `end=` for pagination, not `offset=`** |
| `gamma-api.polymarket.com/markets` | Market metadata | **`condition_ids=` (plural snake) + `closed=true`** required for resolved markets |
| `clob.polymarket.com/prices-history` | Market price time series | Works when market meta is retrievable |

## Trade record shape (data-api /activity)

Each trade includes everything we need without a Gamma join for most analysis:

- `proxyWallet`, `name`, `pseudonym` — trader identity
- `timestamp` — unix seconds (trade entry time)
- `conditionId` — market ID (hex), join key for Gamma
- `asset` — CLOB token ID (big int), join key for prices-history
- `price` — entry price (0–1 probability space)
- `size` — shares
- `usdcSize` — trade USD size
- `side` — BUY / SELL
- `outcomeIndex` — 0 or 1
- `outcome` — "Yes"/"No"/"Over"/team name
- `title` — full market question
- `slug` — market URL slug
- `eventSlug` — event slug, often encodes event date for sports
  (e.g., `j1100-kyo-fag-2026-04-11-more-markets`)

**Implication**: we can infer category (sports/weather/crypto/other) from
`title` + `slug` pattern matching without a Gamma lookup. Gamma is only needed
for market resolve_ts (per-trade fast-settling check) and final settlement
outcome — both cacheable by `conditionId`.

## Critical findings — blocking bugs (all fixed)

### F1: Offset pagination caps at 3500
- HTTP 400 at exactly `offset=3500` for every wallet tried (7 pages × 500).
- Looks like a hard API cap, not trader-specific.
- **Fix verified**: timestamp pagination via `end=<earliest_ts_of_prev_page - 1>`
  works for arbitrary depth (tested to 5,500 trades on one trader, could go deeper).

### F2: Gamma defaults `closed=false`, silently filters dormant markets
- Default `/markets?condition_ids=<cid>` returns empty for resolved markets.
- **Worse**: bad parameter names (`conditionId`, `conditionIds`, `condition_id`)
  silently return the API's default first market ("Russia-Ukraine Ceasefire
  before GTA VI?") instead of erroring. Step 2 must validate the response
  `conditionId` matches the query.
- **Fix verified**: `condition_ids=<cid>&closed=true` returns the correct resolved market.

### F3: "Fast-settling" was mis-defined as market duration
- Original check compared `startDate` to `endDate` — the market's own lifetime.
- Wrong because most sports/event markets are *listed* days-to-weeks before they
  resolve, but traders enter in the final hours. A market with a 7-day lifetime
  that settles 2 hours after a trade is fast-settling *for that trade*.
- **Correct definition**: `resolve_ts - trade_ts ≤ 72h`, computed per-trade.
- Needs Gamma `endDate` lookup (cached by conditionId).

## Strategic findings — who's actually on the leaderboards

### F4: `profit, window=all` is mostly dormant 2024 election punters
Top 5 all-time profit:

| # | Name | Profit | Latest trade | Days active (in our 3500) |
|---|---|---|---|---|
| 1 | Theo4 | $22.0M | 2024-11-13 | 21.7 |
| 2 | Fredi9999 | $16.6M | ~Nov 2024 | 7.5 |
| 3 | kch123 | $11.6M | ~Nov 2024 | 3.2 |
| 4 | Len9311238 | $8.7M | ~Nov 2024 | 13.3 |
| 5 | zxgngl | $7.8M | ~Nov 2024 | 11.8 |

Theo4's top market: "Henry Cavill announced as next James Bond?". These are
not skill traders — they're 2024 election + novelty punters who cashed out
and quit. **Using `window=all` as the seed is wrong. Use `window=30d` or `7d`.**

### F5: `volume, window=all` is automated HFT
Top 5 all-time volume:

| # | Name | Volume | Days in latest 3500 | Implied rate |
|---|---|---|---|---|
| 1 | swisstony | $642M | 0.2 | ~12 trades/minute sustained |
| 2 | risk-manager | $602M | 2.9 | ~1,200/day |
| 3 | gmanas | $529M | 2.0 | ~1,750/day |
| 4 | tripping | $523M | 2.2 | ~1,600/day |
| 5 | cigarettes | $482M | 1.7 | ~2,100/day |

Confirmed trade example from swisstony: "Kyōto Sanga FC vs. Fagiano Okayama
O/U 3.5". Traded at 0.35 for $8.05 on a soccer over/under. Bursts of
hundreds-to-thousands of trades per hour in a single market = almost
certainly a market-making or cross-book arb bot.

**Implication**: volume leaderboard candidates are likely **non-portable** edge
(latency / market-making). Include in the sample to confirm via edge-source
classifier but expect most to fail portability.

## Scale implications for Step 2

- Active traders have **many times more** total trades than the 3500 cap
  suggested. swisstony alone is plausibly at 100k+ lifetime trades.
- Naive "pull everything for top 1000 traders" is probably 10M+ trades and
  hours of API time.
- **Need a tiered pull**: cheap headline pass on all candidates → full pull
  only on ones that look promising on the headline pass.
- Rate limit headroom means we can push to ~10 req/s safely (vs the 3.3 req/s
  we used in Step 1).

## Decision updates for CLAUDE.md

1. Leaderboard seed: use recent windows (7d + 30d profit and volume),
   not `all`.
2. Fast-settling = per-trade (`resolve_ts - trade_ts ≤ 72h`), not per-market.
3. Step 2 uses tiered pulls (cheap headline → full on survivors).
4. Step 2 must validate Gamma responses by matching `conditionId`, not
   trusting `status=200`.
5. Expect many top-volume traders to fail edge-portability; keep them in the
   sample anyway as negative controls.
