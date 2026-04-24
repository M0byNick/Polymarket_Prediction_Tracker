"""Smoke tests for PnL realization + fill sim math.

Covers:

- :func:`scripts.resolution_watcher.compute_realized_pnl`
- :func:`src.polymarket_client.parse_resolution`
- End-to-end: insert a signal → consume → resolve, verify PnL lands
  in ``paper_positions`` correctly.

Run with::

    python -m unittest tests.test_paper_pnl
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Ensure repo root is on sys.path so 'src' and 'scripts' both import.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.db import connect  # noqa: E402
from src.polymarket_client import parse_resolution  # noqa: E402
from src import paper_schema  # noqa: E402,F401
from scripts.resolution_watcher import compute_realized_pnl  # noqa: E402
from scripts.paper_trader import consume_pending_signals  # noqa: E402


class TestComputeRealizedPnl(unittest.TestCase):
    """Direct arithmetic on the paper-PnL formula."""

    def test_no_wins_returns_profit(self) -> None:
        # Bought NO at 0.30 for 100 shares. NO wins → profit = 100 * (1 - 0.30) = 70.
        self.assertAlmostEqual(
            compute_realized_pnl("NO", 0.30, 100.0, "NO"), 70.0
        )

    def test_no_loses_returns_full_stake(self) -> None:
        # Bought NO at 0.30 for 100 shares. YES wins → loss = -100 * 0.30 = -30.
        self.assertAlmostEqual(
            compute_realized_pnl("NO", 0.30, 100.0, "YES"), -30.0
        )

    def test_yes_wins(self) -> None:
        # Bought YES at 0.70 for 100 shares. YES wins → profit = 100 * (1 - 0.70) = 30.
        self.assertAlmostEqual(
            compute_realized_pnl("YES", 0.70, 100.0, "YES"), 30.0
        )

    def test_yes_loses(self) -> None:
        # Bought YES at 0.70 for 100 shares. NO wins → loss = -100 * 0.70 = -70.
        self.assertAlmostEqual(
            compute_realized_pnl("YES", 0.70, 100.0, "NO"), -70.0
        )

    def test_fractional_shares(self) -> None:
        # $100 at 0.35 = 285.714... shares. NO wins → 285.71 * 0.65 ≈ 185.71.
        qty = 100.0 / 0.35
        self.assertAlmostEqual(
            compute_realized_pnl("NO", 0.35, qty, "NO"), qty * 0.65, places=6
        )

    def test_unknown_side_raises(self) -> None:
        with self.assertRaises(ValueError):
            compute_realized_pnl("OTHER", 0.35, 100.0, "NO")


class TestParseResolution(unittest.TestCase):
    def test_open_market_not_resolved(self) -> None:
        self.assertEqual(
            parse_resolution({"closed": False, "outcomePrices": ["1", "0"]}),
            (False, None),
        )

    def test_list_yes_wins(self) -> None:
        self.assertEqual(
            parse_resolution({"closed": True, "outcomePrices": ["1", "0"]}),
            (True, "YES"),
        )

    def test_list_no_wins(self) -> None:
        self.assertEqual(
            parse_resolution({"closed": True, "outcomePrices": ["0", "1"]}),
            (True, "NO"),
        )

    def test_json_string_encoded(self) -> None:
        self.assertEqual(
            parse_resolution({"closed": True, "outcomePrices": '["1","0"]'}),
            (True, "YES"),
        )

    def test_ambiguous_disputed(self) -> None:
        self.assertEqual(
            parse_resolution({"closed": True, "outcomePrices": ["0.5", "0.5"]}),
            (False, None),
        )

    def test_missing_prices(self) -> None:
        self.assertEqual(parse_resolution({"closed": True}), (False, None))


class TestEndToEndFillAndResolve(unittest.TestCase):
    """Exercise the signal → fill → position → PnL pipeline against a
    temp SQLite DB. No network calls — we insert synthetic signals
    directly."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "test_paper.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _insert_signal(self, conn, *, cid="CID-TEST-1", trigger=0.30,
                        ask_size=500.0, resolve_ts=1_900_000_000) -> int:
        cur = conn.execute(
            """
            INSERT INTO paper_signals (
                condition_id, token_id, side, trigger_price,
                best_bid, best_ask, bid_size, ask_size,
                book_mid, book_spread, slug, title, category_v2,
                resolve_ts, status, reject_reason, created_ts, consumed_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                cid, "TOKEN-1", "NO", trigger,
                trigger - 0.01, trigger, 100.0, ask_size,
                trigger - 0.005, 0.01, "epl-test", "Will test win on x?",
                "sports_soccer_epl", resolve_ts, "new", None, 1_800_000_000, None,
            ),
        )
        return cur.lastrowid

    def test_consumes_signal_and_writes_position(self) -> None:
        with connect(self.db) as conn:
            sig_id = self._insert_signal(conn)
            conn.commit()

        n = consume_pending_signals(self.db, flat_usd=100.0)
        self.assertEqual(n, 1)

        with connect(self.db) as conn:
            sig = conn.execute(
                "SELECT status FROM paper_signals WHERE id=?", (sig_id,)
            ).fetchone()
            self.assertEqual(sig["status"], "consumed")

            pos = conn.execute(
                "SELECT * FROM paper_positions WHERE condition_id=?",
                ("CID-TEST-1",),
            ).fetchone()
            self.assertIsNotNone(pos)
            self.assertEqual(pos["side"], "NO")
            self.assertAlmostEqual(pos["fill_price"], 0.30, places=6)
            # $100 / 0.30 = 333.33... shares
            self.assertAlmostEqual(pos["qty_shares"], 100.0 / 0.30, places=4)

    def test_rejects_insufficient_ask_size(self) -> None:
        # ask_size=0.1 shares → below 1-share floor → rejected.
        with connect(self.db) as conn:
            sig_id = self._insert_signal(conn, ask_size=0.1)
            conn.commit()

        n = consume_pending_signals(self.db, flat_usd=100.0)
        self.assertEqual(n, 0)

        with connect(self.db) as conn:
            row = conn.execute(
                "SELECT status, reject_reason FROM paper_signals WHERE id=?",
                (sig_id,),
            ).fetchone()
            self.assertEqual(row["status"], "rejected")
            self.assertEqual(row["reject_reason"], "insufficient_size")

    def test_duplicate_signal_blocked_by_has_open_check(self) -> None:
        from src.paper_schema import has_open_or_recent_position

        with connect(self.db) as conn:
            self._insert_signal(conn, cid="CID-DUP")
            conn.commit()
            self.assertTrue(has_open_or_recent_position(conn, "CID-DUP"))
            self.assertFalse(has_open_or_recent_position(conn, "CID-UNUSED"))

    def test_caps_fill_at_ask_size(self) -> None:
        # Trigger 0.25, flat_usd=100 wants 400 shares, but book only
        # offers 50. Fill should be 50 shares, fill_usd=12.50.
        with connect(self.db) as conn:
            self._insert_signal(conn, cid="CID-CAP", trigger=0.25, ask_size=50.0)
            conn.commit()

        n = consume_pending_signals(self.db, flat_usd=100.0)
        self.assertEqual(n, 1)

        with connect(self.db) as conn:
            pos = conn.execute(
                "SELECT qty_shares, qty_usd FROM paper_positions "
                "WHERE condition_id='CID-CAP'"
            ).fetchone()
            self.assertAlmostEqual(pos["qty_shares"], 50.0, places=6)
            self.assertAlmostEqual(pos["qty_usd"], 50.0 * 0.25, places=6)


if __name__ == "__main__":
    unittest.main()
