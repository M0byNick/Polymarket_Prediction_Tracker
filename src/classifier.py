"""Event-slug classifier.

Polymarket's Gamma ``category`` field is 100% null on our corpus of
66k+ markets, and the ``/events`` endpoint is too slow to call per
market. The workable signal is the event slug itself: slugs are
machine-generated with clean league/sport prefixes
(``nba-lal-det-...``, ``epl-liv-ars-...``, ``btc-up-or-down-...``),
and ~300 first-token patterns cover all 44k+ unique slugs.

This module extracts the classifier from ``notebooks/07_classifier_fix.py``
so production scripts can use it without importing notebook code. Rules
are ordered — more specific prefixes come before catch-alls.

Public API
----------
:func:`classify_slug`
    Map a raw slug to a canonical fine-grained category string.
:func:`broad_category`
    Collapse a fine category into one of ``sports``, ``crypto``,
    ``esports``, ``politics``, ``news``, ``games``, ``weather``,
    ``misc``.
:data:`EURO_SOCCER_CATEGORIES`
    The set of fine categories the Stage 4 primary model rule targets
    (UCL, EPL, Ligue 1, La Liga, Serie A, Bundesliga).
:data:`MONEYLINE_TITLE_RE`
    Compiled regex the signal monitor uses to gate on "Will [team] win
    on [date]?" style question titles.
"""
from __future__ import annotations

import re

# Ordered list of (regex, canonical_category). First match wins. Copied
# verbatim from notebooks/07_classifier_fix.py; keep these two in sync
# whenever new prefixes are added.
SLUG_PREFIX_RULES: list[tuple[str, str]] = [
    # --- Crypto (largest group) --- #
    (r"^btc[-_]", "crypto_btc"),
    (r"^bitcoin[-_]", "crypto_btc"),
    (r"^eth[-_]", "crypto_eth"),
    (r"^ethereum[-_]", "crypto_eth"),
    (r"^xrp[-_]", "crypto_xrp"),
    (r"^sol[-_]", "crypto_sol"),
    (r"^solana[-_]", "crypto_sol"),
    (r"^bnb[-_]", "crypto_bnb"),
    (r"^doge[-_]", "crypto_doge"),
    (r"^dogecoin[-_]", "crypto_doge"),
    (r"^hype[-_]", "crypto_other"),
    (r"^ada[-_]", "crypto_other"),
    (r"^avax[-_]", "crypto_other"),
    (r"^matic[-_]", "crypto_other"),
    (r"^link[-_]", "crypto_other"),
    (r"^dot[-_]", "crypto_other"),
    (r"^ltc[-_]", "crypto_other"),
    (r"^trx[-_]", "crypto_other"),
    (r"^near[-_]", "crypto_other"),
    (r"^atom[-_]", "crypto_other"),
    (r"^tia[-_]", "crypto_other"),
    (r"^sui[-_]", "crypto_other"),
    (r"^inj[-_]", "crypto_other"),
    (r"^sei[-_]", "crypto_other"),
    (r"^apt[-_]", "crypto_other"),
    (r"^op[-_]", "crypto_other"),
    (r"^arb[-_]", "crypto_other"),
    (r"^pepe[-_]", "crypto_other"),
    (r"^shib[-_]", "crypto_other"),
    (r"^wif[-_]", "crypto_other"),
    (r"^bonk[-_]", "crypto_other"),
    (r"^what-price-will-bitcoin", "crypto_btc"),
    (r"^what-price-will-ethereum", "crypto_eth"),
    (r"^what-price-will-solana", "crypto_sol"),
    (r"^what-price-will-xrp", "crypto_xrp"),
    (r"^what-price-will-[a-z]+", "crypto_other"),
    # --- Sports: leagues with unambiguous league-code prefixes --- #
    (r"^nba[-_]", "sports_nba"),
    # NBA team-code prefix with a year suffix (covers LAL-DET-2026-03-04 etc).
    (
        r"^(lal|bos|gsw|mia|nyk|den|dal|phi|mil|phx|lac|mem|chi|nop|sac|"
        r"hou|min|okc|orl|cha|was|atl|det|cle|brk|por|uta|sas|tor|ind)[-_].*\d{4}",
        "sports_nba",
    ),
    (r"^nfl[-_]", "sports_nfl"),
    (r"^cfb[-_]", "sports_ncaa_football"),
    (r"^mlb[-_]", "sports_mlb"),
    (r"^nhl[-_]", "sports_nhl"),
    (r"^cbb[-_]", "sports_ncaa_basketball"),
    (r"^ncaa[-_]", "sports_ncaa"),
    # --- Soccer: European leagues (the Stage 4 primary targets) --- #
    (r"^epl[-_]", "sports_soccer_epl"),
    (r"^efl[-_]", "sports_soccer_efl"),
    (r"^elc[-_]", "sports_soccer_efl_championship"),
    (r"^ucl[-_]", "sports_soccer_ucl"),
    (r"^uel[-_]", "sports_soccer_uel"),
    (r"^bun[-_]", "sports_soccer_bundesliga"),
    (r"^bl2[-_]", "sports_soccer_bundesliga2"),
    (r"^fl1[-_]", "sports_soccer_ligue1"),
    (r"^fl2[-_]", "sports_soccer_ligue2"),
    (r"^ita[-_]", "sports_soccer_seriea"),
    (r"^liga[-_]", "sports_soccer_laliga"),
    (r"^spa[-_]", "sports_soccer_laliga"),
    (r"^lpo[-_]", "sports_soccer_portugal"),
    (r"^ned[-_]", "sports_soccer_eredivisie"),
    (r"^por[-_]", "sports_soccer_portugal"),
    # --- Soccer: Americas / Asia / Oceania / rest of Europe --- #
    (r"^mls[-_]", "sports_soccer_mls"),
    (r"^bra[-_]", "sports_soccer_brazil"),
    (r"^arg[-_]", "sports_soccer_argentina"),
    (r"^mex[-_]", "sports_soccer_mexico"),
    (r"^j\d", "sports_soccer_jleague"),
    (r"^k\d", "sports_soccer_kleague"),
    (r"^a-league", "sports_soccer_aleague"),
    (r"^jup", "sports_soccer_belgium"),
    (r"^tur[-_]", "sports_soccer_turkey"),
    (r"^rus[-_]", "sports_soccer_russia"),
    (r"^gre[-_]", "sports_soccer_greece"),
    (r"^cro[-_]", "sports_soccer_croatia"),
    (r"^cze[-_]", "sports_soccer_czech"),
    (r"^pol[-_]", "sports_soccer_poland"),
    (r"^ukr[-_]", "sports_soccer_ukraine"),
    (r"^aut[-_]", "sports_soccer_austria"),
    (r"^swi[-_]", "sports_soccer_switzerland"),
    (r"^bel[-_]", "sports_soccer_belgium"),
    (r"^nor[-_]", "sports_soccer_norway"),
    (r"^swe[-_]", "sports_soccer_sweden"),
    (r"^den[-_]", "sports_soccer_denmark"),
    (r"^fin[-_]", "sports_soccer_finland"),
    (r"^scp[-_]", "sports_soccer_scotland"),
    (r"^sco[-_]", "sports_soccer_scotland"),
    (r"^spl[-_]", "sports_soccer_scotland"),
    (r"^wc[-_]", "sports_soccer_worldcup"),
    (r"^eur[-_]", "sports_soccer_euros"),
    (r"^nations-league", "sports_soccer_nationsleague"),
    (r"^copa", "sports_soccer_copa"),
    # --- Tennis --- #
    (r"^atp[-_]", "sports_tennis_atp"),
    (r"^wta[-_]", "sports_tennis_wta"),
    (
        r"^.*?(australian-open|french-open|wimbledon|us-open)-.*(winner|tennis)",
        "sports_tennis_grandslam",
    ),
    # --- Combat sports --- #
    (r"^ufc[-_]", "sports_mma_ufc"),
    (r"^pfl[-_]", "sports_mma_pfl"),
    (r"^boxing[-_]", "sports_boxing"),
    # --- Motorsport --- #
    (r"^f1[-_]", "sports_f1"),
    (r"^.*-grand-prix", "sports_f1"),
    (r"^motogp", "sports_motogp"),
    (r"^indycar", "sports_indycar"),
    (r"^nascar", "sports_nascar"),
    # --- Other individual sports --- #
    (r"^pga[-_]", "sports_golf"),
    (r"^liv[-_]", "sports_golf"),
    (r"^masters[-_]", "sports_golf"),
    (r"^the-masters", "sports_golf"),
    (r"^cricket", "sports_cricket"),
    (r"^ipl[-_]", "sports_cricket"),
    (r"^test-match", "sports_cricket"),
    (r"^rugby", "sports_rugby"),
    (r"^nrl[-_]", "sports_rugby"),
    (r"^olympic", "sports_olympics"),
    (r"^summer-olympic", "sports_olympics"),
    (r"^winter-olympic", "sports_olympics"),
    # --- Esports --- #
    (r"^lol[-_]", "esports_lol"),
    (r"^cs2[-_]", "esports_cs2"),
    (r"^csgo[-_]", "esports_csgo"),
    (r"^dota2[-_]", "esports_dota2"),
    (r"^dota[-_]", "esports_dota2"),
    (r"^val[-_]", "esports_valorant"),
    (r"^es2[-_]", "esports_other"),
    (r"^rl[-_]", "esports_rocket_league"),
    (r"^ow[-_]", "esports_overwatch"),
    (r"^sc2[-_]", "esports_sc2"),
    # --- Games / parlor --- #
    (r"^poker", "games_poker"),
    (r"^.*poker", "games_poker"),
    (r"^.*chess", "games_chess"),
    # --- News / events / politics --- #
    (r"^will-", "news_will"),
    (r"^when-", "news_when"),
    (r"^trump[-_]", "politics_us"),
    (r"^biden[-_]", "politics_us"),
    (r"^harris[-_]", "politics_us"),
    (r"^2024-", "politics_2024"),
    (r"^2025-", "misc_2025"),
    (r"^2026-", "misc_2026"),
    (r"^presidential", "politics_us"),
    (r"^senate", "politics_us"),
    (r"^house", "politics_us"),
    (r"^election", "politics_us"),
]

# Pre-compile for hot-path calls from the signal monitor loop.
_COMPILED_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pat), cat) for pat, cat in SLUG_PREFIX_RULES
]

# Fallback: three-letter country code that wasn't matched above.
_SOCCER_COUNTRY_RE = re.compile(
    r"^(ita|spa|por|eng|ger|fra|sco|bel|ned|nor|swe|den|fin|"
    r"rus|ukr|tur|gre|cro|cze|pol|aut|swi|isr)[-_]"
)


def classify_slug(slug: str) -> str:
    """Map a slug to a canonical fine category.

    Returns ``"misc"`` if no rule matches.
    """
    if not slug:
        return "misc"
    s = slug.lower()
    for pattern, canon in _COMPILED_RULES:
        if pattern.match(s):
            return canon
    m = _SOCCER_COUNTRY_RE.match(s)
    if m:
        return f"sports_soccer_{m.group(1)}"
    return "misc"


def broad_category(cat: str) -> str:
    """Collapse fine-grained categories into a broad bucket."""
    if cat.startswith("sports_"):
        return "sports"
    if cat.startswith("crypto"):
        return "crypto"
    if cat.startswith("esports"):
        return "esports"
    if cat.startswith("politics"):
        return "politics"
    if cat.startswith("news"):
        return "news"
    if cat.startswith("games"):
        return "games"
    if cat == "weather":
        return "weather"
    return "misc"


# ---- Stage 4 primary-model targeting --- #
#
# The fade-favorite edge in POLYMARKET_STAGE4_ROADMAP.md is defined over
# these six European top-flight leagues. Expand with care: adding a league
# that has a very different draw rate would change the edge's expected
# ROI. La Liga has lowest historical draws, Ligue 1 highest. Stick to
# the six below until out-of-sample data argues otherwise.

EURO_SOCCER_CATEGORIES: frozenset[str] = frozenset(
    {
        "sports_soccer_ucl",
        "sports_soccer_epl",
        "sports_soccer_ligue1",
        "sports_soccer_laliga",
        "sports_soccer_seriea",
        "sports_soccer_bundesliga",
    }
)

# Moneyline question-shape: "Will <team> win on <date>?".
# Gates out "Will <team> score first?", "Will there be over 2.5 goals?",
# and multi-option-market shapes. The signal monitor requires a match.
MONEYLINE_TITLE_RE: re.Pattern[str] = re.compile(
    r"^Will\s+.+\s+win\s+on\s+.+\?\s*$", re.IGNORECASE
)


def is_primary_model_target(slug: str, title: str) -> bool:
    """True if (slug, title) passes the Stage 4 primary-model gate.

    Combines the category filter with the title regex. Callers must
    additionally check ``resolve_ts`` is in the future and within the
    72h fast-settling window before emitting a signal.
    """
    if classify_slug(slug) not in EURO_SOCCER_CATEGORIES:
        return False
    if not MONEYLINE_TITLE_RE.match(title or ""):
        return False
    return True
