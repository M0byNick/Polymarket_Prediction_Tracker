# Polymarket Stage 4 Roadmap — Paper Trading

**Status date:** 2026-04-19
**Author:** research status sync (phases 0 through 3e complete)
**Purpose:** consolidate trader-portfolio findings, declare portability verdicts
on discovered edges, and scope the minimum viable paper-trading bot that can be
stood up on the existing VPS.

---

## Executive summary

- **207 traders** screened across 8 leaderboards; **17** passed the strict tier
  (≥500 fast-settling trades, bootstrap p<0.05). After fixing the category
  classifier and adding the `trades_per_day > 500` HFT exclusion, **11** remain
  classified as sports specialists.
- **One clearly portable edge has been identified and backtested:
  fade-favorite Euro soccer moneyline NO ≤ 0.35.** Across 1,210 historical
  positions from 42 distinct wallets, 22% gross ROI / ~61% WR / $7.58M PnL.
- **No forward paper test has been run yet.** All validation so far is
  retrospective against closed markets from already-profitable traders.
- **Stage 4 scope**: a paper bot that (a) polls open Euro soccer moneyline
  books every 60 s, (b) writes simulated fills when NO ≤ 0.35, (c) holds to
  resolution and realizes PnL. Target: 100 resolved paper positions before
  any live-capital decision.

---

## 1. Current state of the trader-portfolio analysis

### Filter funnel (after classifier v2)

| Step | Input | Output | Notes |
|---|---|---|---|
| Seed | 8 leaderboards (profit/volume × 1d/7d/30d/all) | 207 unique wallets | Polymarket hard-caps leaderboard at 50 entries — this is the ceiling |
| Tier B pull (≤50k trades each) | 207 | 207 characterized | 52 min runtime |
| Honorable (≥300 trades, ≥150 per counted cat., cat. PnL>0) | 207 | 119 | Pattern-discovery pool only |
| Strict (≥500 trades, ≥250 per counted cat., block-bootstrap p<0.05) | 207 | 17 | Original |
| Classifier v2 + HFT filter (`trades_per_day > 500` dropped) | 17 | **11 sports** + 2 non-sports strict | See drops below |

### HFT bots dropped by classifier v2

- `apucimama`, `Lilybaeum`, `Sharky6999`, `0xc2494b32d6…` — >500 trades/day,
  crypto/esports books, not sports specialists.
- `PeterDeboerCancerPatient` — reclassified as 53% esports, not sports.

### Dropped to non-sports strict (still valid, not primary targets)

- `fengdubiying` — esports specialist.
- `033033033` — deep-certainty (0.999) arbitrage; edge is real but alpha is
  trivial (tail-risk gated; requires tying up capital at >99% prices).

### Priority-4 strict sports survivors (deep-dived)

| Wallet | PnL | Position WR | Positions | Profile | Portable? |
|---|---|---|---|---|---|
| `0x006cc834Cc…` | $4.55M | 49.4% | 502 | Fade-favorite Euro soccer moneyline bettor (originally mis-read as LP) | **Yes — rule-based** |
| `piston777` | $486k | 83.8% | 1,496 | Active trend-follower on NBA + LoL; 4× sizing asymmetry; takes partial profits mid-position | No (discretionary) |
| `SecondWindCapital` | $2.04M | 37.7% | 53 | Early news/event thesis speculator (Iran escalation, UFC PPVs, Alcaraz tennis) | No (editorial) |
| `033033033` | n/a in sports | — | — | Deep-certainty arbitrage baseline | Yes, but trivial alpha |

### Strict-sports pool remaining (6 unexplored)

Not yet deep-dived; candidates include `Supah9ga` (underdog specialist) and
others in the post-classifier sports strict pool. Phase 3c is pulling their
full history for Phase 3f follow-up but is not a Stage 4 blocker.

**Cross-pattern warning:** all three deep-dived whales are concentrated
bettors — top 10 positions = 46-118% of total PnL. Removing the whales, each
is roughly breakeven. The research question "can we identify conviction bets
ex ante?" is still open.

---

## 2. Portability verdict

The project's operating definition of "portable" is: **an edge that can be
expressed as code from publicly-available, real-time data, without requiring
private information, privileged execution speed, or human editorial
judgment.**

### Transferable features (2)

1. **Euro soccer moneyline market filter.** `category_v2 ∈ {UCL, EPL,
   Ligue 1, La Liga, Serie A, Bundesliga}` AND title matches regex
   `^Will .+ win on .+\?$`. Implementable as slug-prefix lookup (302 prefixes
   cover all 44k unique slugs — zero API calls per market — already coded in
   `notebooks/07_classifier_fix.py`).
2. **Price-threshold rule.** Buy NO when NO ask ≤ 0.35 (equivalent to
   market-implied favorite probability ≥ 65%). Hold to resolution. This is a
   pure function of quoted price — no judgment, no external signal needed.

Both features together constitute the **Primary Model Rule**: *"On Polymarket
European soccer 'Will [team] win?' moneyline markets, buy NO when NO ask ≤
0.35. Hold to resolution."*

### Non-portable features (5)

1. **Sizing asymmetry** (piston777's 4× winners vs losers). Requires active
   in-position management; no rule-based trigger has been found.
2. **Active trend-following / mid-position exits** (piston777). Entry price
   of 0.99 with SELLs mid-position is not reducible to a stateless rule.
3. **Editorial news thesis** (SecondWindCapital). Iran escalation, UFC PPV,
   Alcaraz — requires human judgment about which stories will re-price which
   markets. No systematic analog.
4. **NCAA basketball YES edge** (0x006cc834Cc, $1.0M of the $4.42M). Mechanism
   unclarified; left as a Phase 3g TODO, not in Stage 4 scope.
5. **Latency / HFT patterns** (apucimama, Sharky6999, 0xc2494b32d6…,
   Lilybaeum). >500 trades/day book-shaping; not replicable without
   co-located infra.

### Sample-size & statistical concerns

- **Survivorship bias is baked in.** The strict filter selects on realized
  profitability; any rule extracted from a single winner's positions must
  cross-validate.
- **Cross-trader validation has been done** for the fade-favorite rule: 332
  similar NO-side Euro soccer moneyline positions across 25 honorable-tier
  traders showed 48.8% WR, 21% ROI, $4.9M PnL. Combined with 0x006cc834Cc
  this is **1,210 positions, $34.26M staked, $7.58M PnL, ~22% gross ROI,
  ~61% WR**.
- **The edge is draw-driven, not upset-driven.** European soccer has a
  ~25–30% draw rate. In "Will [team] win?" markets, draws resolve NO. Of
  0x006cc834Cc's 8 winning fade-favorite positions, 4 came from draws and
  4 from outright losses. This is the mechanistic explanation — the market
  systematically underweights the draw leg on heavy favorites.
- **Not yet validated:** whether sharp-book consensus (DraftKings moneyline
  via ESPN) also flags Polymarket's heavy-favorite prices as mispriced, or
  whether the mispricing is only visible after accounting for the draw leg.
  `notebooks/13_espn_sharp_book_join.py` scaffolded the ESPN pull but the
  implied-probability comparison table is still unimplemented (the
  `moneyline_to_probability` helper is defined but not called).

---

## 3. Forward-test status

**A single paper trade has not been placed.** All validation to date is
retrospective against closed markets from the top-1000 wallets.

### What has been validated

| Validation | N | WR | ROI | PnL | Notes |
|---|---|---|---|---|---|
| 0x006cc834Cc fade-favorite backtest | 24 | 62.5% | 67% | +$2.53M | Single wallet; in-sample |
| Honorable cross-validation | 332 | 48.8% | 21% | +$4.9M | 25 other wallets |
| Combined (post-classifier re-run) | **1,210** | **~61%** | **22%** | **+$7.58M** | 42 wallets, Euro soccer only |
| ESPN ground-truth consistency (0x006cc834Cc subset) | 20 joined, 19 matched | — | — | — | 12 ✓ consistent, 0 inconsistent |

### Blockers preventing forward test right now

1. **Snapshot cadence is too slow for the last-hour entry window.** The VPS
   container runs `snapshot_markets.py` every 30 min but wall-clock time is
   40–60 min against ~10k open markets — so effective cadence is ≈90 min.
   The primary-model rule can hit any time a price moves to ≤ 0.35, but
   many of 0x006cc834Cc's fills occurred in the final hour before
   resolution where 90-min stale books are useless.
2. **No slug classifier deployed in the container.** `07_classifier_fix.py`
   lives in the notebooks tree; Stage 4 needs the 302-prefix table reachable
   from a production script to pre-filter the whitelist.
3. **No signal monitor.** No code detects "NO price on a qualifying market
   just crossed 0.35."
4. **No paper-order writer.** No table, no fill simulator, no position
   tracker.
5. **No resolution watcher.** Gamma `closed=true` polling exists in Phase 1
   notebooks but no scheduled process realizes PnL against held paper
   positions.
6. **Reusable infra (`db.py`, `log.py`, `polymarket_client.py`) referenced
   in CLAUDE.md is NOT in this repo.** These live only in the sibling
   `../Prediction_Markets/` project and must be ported (or imported via
   path) as a pre-requisite to any Stage 4 code.

---

## 4. Stage 4 plan — minimum viable paper bot

### Scope (explicit)

- One signal: fade-favorite Euro soccer moneyline NO ≤ 0.35.
- One sizing rule: flat $100 notional per position (paper dollars).
- One exit rule: hold to resolution, no early exits.
- Paper only. No live orders, no real capital at risk.
- No Telegram alerts at MVP (can be added as a fast follow-up).
- No secondary signals (NCAA basketball, piston777-derived features, etc.)
  until the primary signal has 100 resolved paper positions.

### Signal logic

```
for each open Polymarket market M:
    if not slug_classifier(M.slug) in {UCL, EPL, Ligue 1, La Liga, Serie A, Bundesliga}:
        skip
    if not regex_match(M.title, r"^Will .+ win on .+\?$"):
        skip
    if M.resolve_ts - now > 72h:
        skip
    fetch CLOB book for M.clob_token_no
    if book.best_ask <= 0.35 and book.ask_size >= $100 notional:
        emit_signal(M, side=NO, trigger_price=book.best_ask, ts=now)
```

### Market filter

- Whitelist built once per cycle from Gamma `/markets?closed=false`.
- Slug classifier ported from `07_classifier_fix.py` as
  `src/classifier.py`; 302 prefix lookup, zero API calls per classification.
- Title regex gates out non-moneyline questions ("will X score", "over/under
  2.5 goals", etc.) that the slug prefix alone would let through.

### Sizing

- **MVP**: flat $100 per signal — one position per condition_id, no
  pyramiding. Caps exposure and keeps Sharpe math unambiguous for the
  first 100 positions.
- **Post-MVP**: fractional-Kelly proportional to `(0.35 - ask) / 0.35`
  (deeper mispricing → bigger size), capped at 2% of paper bankroll.
  Defer until rule is validated.

### Execution path (paper)

1. `signal_monitor.py` writes a row to `paper_signals` (condition_id,
   token_id, trigger_price, ask_size, book_mid, book_spread, ts).
2. `paper_trader.py` reads new signals, writes a `paper_orders` row with
   `status='submitted'`, `limit_price=trigger_price`, `qty=100/trigger_price`
   (shares).
3. Fill simulator assumes a **taker fill at the recorded ask** for MVP
   (conservative — real passive fills would be cheaper but harder to model;
   deferred). Writes a `paper_positions` row with `status='open'`,
   `fill_price=trigger_price`, `fill_ts=now`.
4. No partial fills, no order cancellation, no price-impact model at MVP.

### Position tracking to resolution

- `resolution_watcher.py` runs every 30 min.
- For each `paper_positions` row with `status='open'`, query Gamma
  `/markets?condition_ids=<cid>&closed=true`. If closed, read `outcome`
  field ("Yes"/"No") and realize PnL:
  - NO position wins (outcome == "No"): PnL = `qty × (1 - fill_price)`.
  - NO position loses (outcome == "Yes"): PnL = `-qty × fill_price`.
- Write to `paper_positions` with `status='closed'`, `resolved_outcome`,
  `realized_pnl`, `resolved_ts`.

### Data model additions

```sql
paper_signals     (id, condition_id, token_id, trigger_price, ask_size,
                   book_mid, book_spread, ts_utc, slug, title)
paper_orders      (id, signal_id, condition_id, token_id, side, limit_price,
                   qty, status, submitted_ts)
paper_positions   (id, order_id, condition_id, token_id, side, fill_price,
                   qty, fill_ts, status, resolved_outcome, realized_pnl,
                   resolved_ts, close_ts)
```

Co-located in `data/polymarket_snapshots.db` (reuse the container's SQLite
WAL). No new DB file.

### Success criteria (gate for Stage 5 live trading)

- **≥ 100 resolved paper positions.**
- Out-of-sample (forward) ROI ≥ 15% (half of the backtest 22%, assuming
  real-world slippage and post-publication decay).
- Out-of-sample WR ≥ 50% (coin-flip floor; edge comes from draw skew not WR).
- Sharpe (position-level, no time weighting) ≥ 0.5.
- No single position > 20% of total PnL (guards against 0x006cc834Cc-style
  whale dependence).
- Zero execution bugs in the fill simulator (spot-check 10 random
  positions against recorded book state).

Expected timeline: **~3-6 weeks of Euro soccer season** to accumulate 100
positions at 5-15 qualifying matches/day. Earlier decision if results are
clearly positive or clearly negative after 30 positions.

### Explicitly out of scope for Stage 4

- Sharp-book (DraftKings) cross-validation — can run in parallel as a
  research task but not a gate.
- NCAA basketball YES edge from 0x006cc834Cc.
- Tennis underdog exploration (SecondWindCapital-adjacent).
- Kelly sizing, position correlation, portfolio-level risk model.
- Real-money execution path (CLOB order signing, L1 gas management).
- WebSocket subscriptions — 60 s polling on the whitelist (≤ 50 markets at
  peak) is fine at 10 req/s rate limit.

---

## 5. Build order (for when the user greenlights implementation)

In priority order — each step is a discrete PR-sized commit:

1. **Port shared infra.** Copy `db.py`, `log.py`, `polymarket_client.py`
   from `../Prediction_Markets/` into `src/`. Keep the import surface
   small — Gamma + CLOB + Data API wrappers with shared rate-limiting.
2. **Slug classifier module.** Extract the 302-prefix table from
   `notebooks/07_classifier_fix.py` into `src/classifier.py` with
   `classify_slug(slug) → category_v2` function. Unit test against a
   snapshot of 200 known slugs.
3. **Signal monitor.** `scripts/signal_monitor.py`: polls the whitelist at
   60 s, writes `paper_signals`, logs trigger events.
4. **Paper trader + fill sim.** `scripts/paper_trader.py`: consumes
   `paper_signals`, writes `paper_orders`, simulates taker fill, writes
   `paper_positions`.
5. **Resolution watcher.** `scripts/resolution_watcher.py`: 30-min cron,
   queries Gamma closed markets, realizes PnL.
6. **Dashboard (optional).** Streamlit page reading `paper_positions` —
   running PnL, position list, hit rate. Reuse sibling project's pattern.
7. **Deploy.** Extend the Docker image to run all three processes
   (signal_monitor as a 60s loop, paper_trader as an event-driven consumer,
   resolution_watcher as a cron).
8. **Let it run. Don't tune. Don't add features.** First decision point is
   at 100 resolved positions.

---

## 6. Open research items (parallel, not Stage 4 blockers)

- Finish `13_espn_sharp_book_join.py` — call `moneyline_to_probability` in
  `main()` and produce the Polymarket-vs-DraftKings implied-prob delta
  table. Tests whether the fade edge is draw-leg mispricing or general
  favorite overpricing.
- Deep-dive the 6 unexplored strict-sports survivors after Phase 3c
  completes the full-history pull.
- Resolve the $1.0M NCAA basketball YES edge (0x006cc834Cc).
- Characterize slippage for passive vs taker fills using the snapshot
  book history — informs whether taker-at-ask is too pessimistic.

---

## 7. Risks to the primary thesis

- **Edge decays once it's code-implementable.** The fade-favorite rule is
  simple. If it really delivers 22% ROI, market participants will arb it
  out. Forward test may show a shrinking edge; plan to re-estimate monthly.
- **Survivorship.** 42 wallets × 1,210 positions is not a random sample —
  all wallets were selected because they're profitable. The real
  population-wide edge is likely smaller.
- **Regime dependence.** All historical data is in 2024–2026 when
  Polymarket liquidity was growing rapidly. Deeper books may have priced
  draws more correctly; a backtest on the 2023 snapshot is not feasible
  (snapshot history only starts when our cron does).
- **Draw-rate model risk.** Some leagues / seasons have atypical draw
  rates. A rule that ignores league-level base rates will be under-sized
  on Ligue 1 (higher draws historically) and over-sized on La Liga
  (lower). Post-MVP refinement.
- **Execution-cost drag.** Paper assumes taker-at-ask. Real CLOB fills
  cost gas + spread. On $100 positions the gas drag alone could be 2-5%
  per round trip on L2.

---

## Appendix — key file pointers

- Findings: [notebooks/phase1_findings.md](notebooks/phase1_findings.md),
  [notebooks/phase2_findings.md](notebooks/phase2_findings.md),
  [notebooks/phase2e_comparison.md](notebooks/phase2e_comparison.md),
  [notebooks/phase3a_findings.md](notebooks/phase3a_findings.md).
- Backtest code: [notebooks/12_fade_favorite_model.py](notebooks/12_fade_favorite_model.py).
- ESPN join (unfinished): [notebooks/13_espn_sharp_book_join.py](notebooks/13_espn_sharp_book_join.py).
- Classifier to port: [notebooks/07_classifier_fix.py](notebooks/07_classifier_fix.py).
- Snapshot infra: [scripts/snapshot_markets.py](scripts/snapshot_markets.py),
  [scripts/snapshot_runner.py](scripts/snapshot_runner.py).
- Master project log: [CLAUDE.md](CLAUDE.md).
