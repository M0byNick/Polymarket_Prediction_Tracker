# Polymarket Trader Portfolio Analysis

A research project that looks for Polymarket traders whose edge is both statistically real
and **portable** — reproducible by someone without their latency, their information, or their
access — and then tries to reverse-engineer the features behind it.

**Author:** Nicholas Morihisa ([github.com/M0byNick](https://github.com/M0byNick) ·
[linkedin.com/in/nmorihisa](https://www.linkedin.com/in/nmorihisa/))

## At a glance

| | |
|---|---|
| **What it does** | Screens Polymarket traders for statistically significant, portable edge |
| **Method** | Hypothesis testing and revealed-preference classifiers, not PnL ranking |
| **Scope** | Fast-settling markets only (≤72h): sports, weather, short crypto brackets |
| **Language** | Python |
| **Stack** | Polymarket API, SQLite, Jupyter, Docker |
| **Status** | Research. Findings written up alongside the analysis scripts. |

## Why not just rank traders by profit

Because PnL ranking selects for variance. In a population of thousands of traders over a few
months, the top of the leaderboard is dominated by people who got lucky in size, and copying
them is copying noise.

Worse, some genuinely profitable traders have edges you cannot use: latency advantages,
insider information, or a news feed you do not have. An edge that is real but not portable is
worthless to a copier — it will not survive the transfer.

So the screen is built around two questions asked in order: **is the edge statistically
significant**, and **is it portable**. A trader passes only on both.

## Scope: fast-settling only

Restricted to markets resolving within 72 hours:

- **Sports** — NBA, NFL, MLB, NHL, CFB, tennis
- **Weather** — daily highs, precipitation
- **Short crypto brackets** — minute-to-hour BTC/ETH/SOL price levels

**Excluded:** politics, Fed/CPI, and long-horizon markets. They settle too slowly to learn
from and rarely have clean, verifiable external ground truth. Fast feedback loops beat broad
coverage when the goal is to learn something before the season ends.

## Layout

```
scripts/     snapshot_markets.py, snapshot_runner.py — scheduled market capture
notebooks/   Sequenced analysis, each with its findings written up alongside:
               01_feasibility            → 01_feasibility_findings.md
               02_analysis / 02_characterization → 02_final_findings.md
               03_deep_dive_strict       strict-tier trader deep dive
               04_outlier_verification   checking whether outliers survive scrutiny
               05_deep_dive_analysis
               06_honorable_pattern_mining
               07_classifier_fix
data/        Captured snapshots
```

The numbering is chronological and the findings files are kept even where the conclusion was
negative — `04_outlier_verification` exists because outliers that look like edge usually are
not, and that needed checking rather than assuming.

## Setup

```bash
pip install -r requirements.txt
python scripts/snapshot_runner.py
```

Containerized via the included `Dockerfile`.

## Related

Sibling project: [Kalshi_Prediction_Tracker](https://github.com/M0byNick/Kalshi_Prediction_Tracker).
Shares infrastructure patterns (SQLite WAL, logging, alerting, paper trading) but runs a
separate database and schedule.
