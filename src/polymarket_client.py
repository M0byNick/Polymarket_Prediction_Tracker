"""Narrow Polymarket REST client.

Wraps the handful of Gamma + CLOB endpoints the Stage 4 paper bot needs.
Returns raw parsed JSON — higher-level parsing lives in the caller.

Endpoints wrapped
-----------------
Gamma ``/markets``
    List + filter (``closed``, ``active``, ``condition_ids``). Paginated
    via ``offset``/``limit``. The helper :meth:`iter_open_markets`
    iterates all pages transparently.
Gamma ``/markets/{condition_id}``
    Single-market lookup by Polymarket's internal ID (rarely used —
    prefer ``condition_ids=``).
CLOB ``/book``
    Full L2 order book for a given token. Returns bids/asks arrays.

Rate limiting
-------------
A single :class:`RateLimiter` is shared across all calls from one client
instance. Default 10 req/s matches the sibling project and the existing
snapshot script. Pass ``rate=`` to :func:`PolymarketClient` to override.

Error handling
--------------
All methods return ``None`` on HTTP error (with a warning log) rather
than raising. This is deliberate for long-running polling loops — one
flaky response should not crash the loop. Callers must check for ``None``
before dereferencing.

Timeouts are enforced via ``requests``' ``timeout=`` keyword; the default
is 15 s per call.
"""
from __future__ import annotations

import time
from typing import Any, Iterator

import requests

from .log import get_logger

log = get_logger("polymarket_client")

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

DEFAULT_TIMEOUT = 15.0
DEFAULT_RATE = 10.0
DEFAULT_PAGE_SIZE = 500


class RateLimiter:
    """Simple token-bucket-equivalent: enforces min-interval between calls."""

    def __init__(self, rate: float):
        self.min_interval = 1.0 / rate if rate > 0 else 0.0
        self.last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        delta = self.last + self.min_interval - now
        if delta > 0:
            time.sleep(delta)
        self.last = time.monotonic()


class PolymarketClient:
    """Wraps Gamma + CLOB with a shared rate limiter and error logging."""

    def __init__(
        self,
        rate: float = DEFAULT_RATE,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ):
        self.rl = RateLimiter(rate)
        self.timeout = timeout
        self.session = session or requests.Session()

    # ---- Gamma ---- #

    def _gamma_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.rl.wait()
        try:
            r = self.session.get(
                f"{GAMMA_API}{path}",
                params={k: v for k, v in (params or {}).items() if v is not None},
                timeout=self.timeout,
            )
        except requests.RequestException as ex:
            log.warning("gamma %s failed: %s", path, ex)
            return None
        if r.status_code != 200:
            log.warning("gamma %s returned %s", path, r.status_code)
            return None
        try:
            return r.json()
        except ValueError:
            log.warning("gamma %s non-JSON body", path)
            return None

    def iter_open_markets(
        self,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int | None = None,
    ) -> Iterator[dict]:
        """Paginate Gamma ``/markets?closed=false&active=true``.

        Yields individual market dicts. Stops when a page returns fewer
        than ``page_size`` rows (end-of-feed convention) or when
        ``max_pages`` is hit.
        """
        offset = 0
        pages = 0
        while True:
            batch = self._gamma_get(
                "/markets",
                {"closed": "false", "active": "true",
                 "limit": page_size, "offset": offset},
            )
            if not isinstance(batch, list) or not batch:
                return
            yield from batch
            pages += 1
            if len(batch) < page_size:
                return
            if max_pages is not None and pages >= max_pages:
                return
            offset += page_size

    def get_markets_by_condition_ids(
        self, condition_ids: list[str], closed: bool | None = None
    ) -> list[dict]:
        """Fetch a batch of markets by their condition IDs.

        Gamma accepts repeated ``condition_ids=`` query params. We chunk
        at 100 IDs per call to keep URLs reasonable.
        """
        out: list[dict] = []
        chunk = 100
        for i in range(0, len(condition_ids), chunk):
            params: list[tuple[str, str]] = [
                ("condition_ids", cid) for cid in condition_ids[i : i + chunk]
            ]
            if closed is not None:
                params.append(("closed", "true" if closed else "false"))
            self.rl.wait()
            try:
                r = self.session.get(
                    f"{GAMMA_API}/markets", params=params, timeout=self.timeout
                )
            except requests.RequestException as ex:
                log.warning("gamma batch lookup failed: %s", ex)
                continue
            if r.status_code != 200:
                log.warning("gamma batch returned %s", r.status_code)
                continue
            try:
                batch = r.json()
            except ValueError:
                log.warning("gamma batch non-JSON body")
                continue
            if isinstance(batch, list):
                out.extend(batch)
        return out

    # ---- CLOB ---- #

    def get_book(self, token_id: str) -> dict | None:
        """Fetch full L2 book for a CLOB token.

        Returns the raw dict with ``bids`` / ``asks`` arrays, or ``None``
        on error.
        """
        self.rl.wait()
        try:
            r = self.session.get(
                f"{CLOB_API}/book",
                params={"token_id": token_id},
                timeout=self.timeout,
            )
        except requests.RequestException as ex:
            log.warning("clob /book %s failed: %s", token_id[:8], ex)
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None


def top_of_book(
    book: dict | None,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Extract (best_bid_price, best_bid_size, best_ask_price, best_ask_size).

    Polymarket CLOB convention: bids sorted descending by price (best is
    index 0), asks sorted ascending (best is index 0). Returns ``None``
    for any side that is empty.
    """
    if not book:
        return None, None, None, None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    best_bid_p = float(bids[0]["price"]) if bids else None
    best_bid_s = float(bids[0]["size"]) if bids else None
    best_ask_p = float(asks[0]["price"]) if asks else None
    best_ask_s = float(asks[0]["size"]) if asks else None
    return best_bid_p, best_bid_s, best_ask_p, best_ask_s


def parse_resolution(market: dict) -> tuple[bool, str | None]:
    """Parse a Gamma market dict for final resolution.

    Returns ``(is_resolved, outcome)`` where:

    - ``is_resolved`` is ``True`` only when the market is closed AND has
      a deterministic binary outcome. Disputed / frozen markets return
      ``(False, None)``.
    - ``outcome`` is one of ``"YES"`` / ``"NO"`` or ``None``.

    Polymarket encodes outcome via ``outcomePrices``: a length-2 array
    like ``["1", "0"]`` (YES won) or ``["0", "1"]`` (NO won). Some
    responses pack it as a JSON string, others as a real list.
    """
    if not market.get("closed"):
        return False, None
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        import json

        try:
            prices = json.loads(prices)
        except ValueError:
            return False, None
    if not isinstance(prices, (list, tuple)) or len(prices) != 2:
        return False, None
    try:
        yes_p = float(prices[0])
        no_p = float(prices[1])
    except (TypeError, ValueError):
        return False, None
    if abs(yes_p - 1.0) < 1e-6 and abs(no_p) < 1e-6:
        return True, "YES"
    if abs(no_p - 1.0) < 1e-6 and abs(yes_p) < 1e-6:
        return True, "NO"
    # Ambiguous (e.g. 0.5/0.5 disputed). Do not resolve.
    return False, None
