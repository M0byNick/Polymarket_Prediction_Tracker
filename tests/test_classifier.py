"""Smoke tests for src.classifier.

Focuses on the classes that the Stage 4 primary model rule cares about
(Euro soccer moneylines) plus a handful of regression cases for HFT
categories we explicitly want to exclude.

Run with::

    python -m unittest tests.test_classifier
"""
from __future__ import annotations

import unittest

from src.classifier import (
    EURO_SOCCER_CATEGORIES,
    MONEYLINE_TITLE_RE,
    broad_category,
    classify_slug,
    is_primary_model_target,
)


class TestClassifySlug(unittest.TestCase):
    def test_euro_soccer_league_prefixes(self) -> None:
        cases = [
            ("epl-liv-ars-2026-04-12", "sports_soccer_epl"),
            ("ucl-psg-manc-2026-03-04", "sports_soccer_ucl"),
            ("fl1-psg-lyon-2026-02-20", "sports_soccer_ligue1"),
            ("liga-rma-bar-2026-04-05", "sports_soccer_laliga"),
            ("spa-atm-sev-2026-01-15", "sports_soccer_laliga"),
            ("ita-juv-int-2026-03-10", "sports_soccer_seriea"),
            ("bun-bvb-fcb-2026-03-22", "sports_soccer_bundesliga"),
        ]
        for slug, expected in cases:
            with self.subTest(slug=slug):
                self.assertEqual(classify_slug(slug), expected)

    def test_crypto_prefixes(self) -> None:
        self.assertEqual(classify_slug("btc-up-or-down-2026-04-12"), "crypto_btc")
        self.assertEqual(classify_slug("eth-hourly-close-above"), "crypto_eth")
        self.assertEqual(classify_slug("sol-20250412"), "crypto_sol")
        self.assertEqual(classify_slug("what-price-will-bitcoin-hit"), "crypto_btc")

    def test_esports_prefixes(self) -> None:
        self.assertEqual(classify_slug("lol-t1-drx-2026-04-10"), "esports_lol")
        self.assertEqual(classify_slug("cs2-navi-faze-2026-03-20"), "esports_cs2")
        self.assertEqual(classify_slug("val-sen-100t-2026-04-01"), "esports_valorant")

    def test_misc_fallback(self) -> None:
        self.assertEqual(classify_slug(""), "misc")
        self.assertEqual(classify_slug("random-event-no-rule-matches"), "misc")

    def test_nba_team_code_with_year(self) -> None:
        self.assertEqual(classify_slug("lal-det-2026-03-04"), "sports_nba")
        self.assertEqual(classify_slug("gsw-bos-2026-04-01"), "sports_nba")

    def test_country_code_fallback(self) -> None:
        # Country codes not explicitly covered above fall through to the
        # sports_soccer_<code> fallback.
        self.assertEqual(classify_slug("isr-mtk-hap-2026-04-12"), "sports_soccer_isr")


class TestBroadCategory(unittest.TestCase):
    def test_sports_collapse(self) -> None:
        self.assertEqual(broad_category("sports_soccer_epl"), "sports")
        self.assertEqual(broad_category("sports_nba"), "sports")
        self.assertEqual(broad_category("sports_tennis_atp"), "sports")

    def test_non_sports_collapse(self) -> None:
        self.assertEqual(broad_category("crypto_btc"), "crypto")
        self.assertEqual(broad_category("esports_lol"), "esports")
        self.assertEqual(broad_category("news_will"), "news")
        self.assertEqual(broad_category("politics_us"), "politics")
        self.assertEqual(broad_category("games_poker"), "games")
        self.assertEqual(broad_category("misc"), "misc")


class TestEuroSoccerTargetingSet(unittest.TestCase):
    def test_expected_members(self) -> None:
        expected = {
            "sports_soccer_ucl",
            "sports_soccer_epl",
            "sports_soccer_ligue1",
            "sports_soccer_laliga",
            "sports_soccer_seriea",
            "sports_soccer_bundesliga",
        }
        self.assertEqual(EURO_SOCCER_CATEGORIES, frozenset(expected))

    def test_euro_soccer_leagues_classify_into_set(self) -> None:
        # Each representative slug must classify into a category that's
        # in EURO_SOCCER_CATEGORIES — the link between classifier and
        # targeting set is the Stage 4 contract.
        reps = [
            "ucl-psg-manc-2026-03-04",
            "epl-liv-ars-2026-04-12",
            "fl1-psg-lyon-2026-02-20",
            "liga-rma-bar-2026-04-05",
            "ita-juv-int-2026-03-10",
            "bun-bvb-fcb-2026-03-22",
        ]
        for slug in reps:
            with self.subTest(slug=slug):
                self.assertIn(classify_slug(slug), EURO_SOCCER_CATEGORIES)


class TestMoneylineTitle(unittest.TestCase):
    def test_matches(self) -> None:
        for title in [
            "Will Liverpool win on April 12?",
            "Will PSG win on 2026-02-20?",
            "Will Real Madrid win on Sunday April 5?",
            "will arsenal win on 2026-04-12?",  # lowercase should match
        ]:
            with self.subTest(title=title):
                self.assertIsNotNone(MONEYLINE_TITLE_RE.match(title))

    def test_rejects(self) -> None:
        for title in [
            "Will Liverpool score first on April 12?",
            "Over 2.5 goals in Liverpool vs Arsenal?",
            "Will both teams score?",
            "PSG vs Man City: who advances?",
            "",
        ]:
            with self.subTest(title=title):
                self.assertIsNone(MONEYLINE_TITLE_RE.match(title))


class TestIsPrimaryModelTarget(unittest.TestCase):
    def test_accepts_euro_soccer_moneyline(self) -> None:
        self.assertTrue(
            is_primary_model_target(
                "epl-liv-ars-2026-04-12", "Will Liverpool win on April 12?"
            )
        )
        self.assertTrue(
            is_primary_model_target(
                "ucl-psg-manc-2026-03-04", "Will PSG win on March 4?"
            )
        )

    def test_rejects_non_euro_league(self) -> None:
        # NBA slug, otherwise valid moneyline title.
        self.assertFalse(
            is_primary_model_target(
                "nba-lal-bos-2026-03-04", "Will Lakers win on March 4?"
            )
        )
        # MLS slug, otherwise valid title.
        self.assertFalse(
            is_primary_model_target(
                "mls-lafc-atlutd-2026-05-12", "Will LAFC win on May 12?"
            )
        )

    def test_rejects_non_moneyline_title(self) -> None:
        # EPL slug, but title is for over/under, not moneyline.
        self.assertFalse(
            is_primary_model_target(
                "epl-liv-ars-2026-04-12",
                "Will the Liverpool-Arsenal game go over 2.5 goals?",
            )
        )


if __name__ == "__main__":
    unittest.main()
