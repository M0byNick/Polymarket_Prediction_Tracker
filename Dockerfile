# ── Environment Variables ──
# Optional: SNAPSHOT_INTERVAL (default 1800 = 30 min between cycles)
# Optional: SNAPSHOT_HOURS (default 72 = fast-settling horizon)
# Optional: SNAPSHOT_RATE (default 10.0 = requests/sec)
# Optional: SNAPSHOT_MIN_VOL (default 500 = min 24h volume USD)
# Optional: SNAPSHOT_MIN_LIQ (default 500 = min liquidity USD)

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Python deps (cached layer — only requests)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY scripts/ ./scripts/
COPY src/ ./src/
COPY notebooks/ ./notebooks/
COPY CLAUDE.md ./
COPY POLYMARKET_STAGE4_ROADMAP.md ./

# Data directory for volumes
RUN mkdir -p /app/data

# Non-root user
RUN useradd --create-home appuser && \
    chown -R appuser:appuser /app
USER appuser

# Health check (file-based heartbeat, updated at end of each snapshot cycle).
# Threshold is 90 min (not 45) to tolerate the actual cycle time:
# snapshot fetch has scaled to ~40-60 min against ~10k open markets
# (ramped up from the initial ~1k), plus the 30-min inter-cycle sleep.
# If heartbeat hasn't been touched in 90 min, the bot has genuinely stalled.
# Interval bumped 120s → 600s since a 60-90 min cycle doesn't need 2-min polling.
HEALTHCHECK --interval=600s --timeout=10s --retries=3 \
  CMD test -f /app/data/.heartbeat && \
      find /app/data/.heartbeat -mmin -90 | grep -q . || exit 1

# Stage 4 supervisor: runs snapshot_runner + signal_monitor + paper_trader
# + resolution_watcher as isolated subprocesses. Falls back to snapshot-only
# if STAGE4=0 is set (useful for the historical snapshot-only deployment).
CMD ["sh", "-c", "if [ \"${STAGE4:-1}\" = \"0\" ]; then exec python -u scripts/snapshot_runner.py; else exec python -u scripts/stage4_runner.py; fi"]
