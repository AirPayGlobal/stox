"""
News-driven opportunity scanner.

Two modes of operation
-----------------------
1. Boost existing candidates
   apply_news_boost(candidates) re-ranks (symbol, signal, score) lists by
   adding a news sentiment bonus to the technical score.

2. Discover new opportunities
   find_news_catalysts() scans ALL recent market news (not just the watchlist),
   extracts symbols mentioned in strongly positive headlines, and returns them
   ranked by news score. main.py then runs technical analysis on those symbols
   and adds them to the buy queue if they also pass the chart filters.

Catalyst categories that earn extra weight
-------------------------------------------
  +++ earnings beat, revenue beat, raised guidance, buyback, dividend raise,
      FDA approval, merger/acquisition at premium, analyst upgrade, new contract
  --- earnings miss, revenue miss, guidance cut, FDA rejection, layoffs,
      downgrade, legal action, tariff impact, executive departure
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Keyword scoring
# ---------------------------------------------------------------------------

# (pattern, score) — matched against lowercased headline
_CATALYST_RULES: list[tuple[str, int]] = [
    # Strong positives (+3)
    (r"\bearnings beat\b",          3),
    (r"\bbeats? estimates?\b",      3),
    (r"\braises? guidance\b",       3),
    (r"\bfda approv",               3),
    (r"\bbuyback\b",                3),
    (r"\bspecial dividend\b",       3),
    (r"\bacquir",                   2),
    (r"\bmerger\b",                 2),
    (r"\bupgrade[sd]?\b",           2),
    (r"\bprice target raised\b",    3),
    (r"\brecord (revenue|profit|earnings|quarter)\b", 3),
    (r"\bsurpass",                  2),
    (r"\bexceed",                   2),
    (r"\bnew contract\b",           2),
    (r"\bpartnership\b",            1),
    (r"\bbreakthrough\b",           2),
    # Moderate positives (+1)
    (r"\bbeat\b",                   1),
    (r"\bstrong (results|demand|growth|quarter)\b", 2),
    (r"\bgrowth\b",                 1),
    (r"\boptimistic\b",             1),
    (r"\bpositive\b",               1),
    (r"\brally\b",                  1),
    (r"\bsurge[sd]?\b",             1),
    (r"\bjump[sed]?\b",             1),
    (r"\bgain[sed]?\b",             1),
    # Strong negatives (-3)
    (r"\bearnings miss\b",         -3),
    (r"\bmisses? estimates?\b",    -3),
    (r"\bcuts? guidance\b",        -3),
    (r"\bfda reject",              -3),
    (r"\blayoff[s]?\b",            -2),
    (r"\bdowngrade[sd]?\b",        -3),
    (r"\binvestigation\b",         -2),
    (r"\bbankruptcy\b",            -3),
    (r"\bfraud\b",                 -3),
    (r"\brecall\b",                -2),
    (r"\blawsuit\b",               -2),
    (r"\btariff[s]?\b",            -2),
    (r"\bsanction[s]?\b",          -2),
    # Moderate negatives (-1)
    (r"\bwarn(s|ing)?\b",          -2),
    (r"\bdecline[sd]?\b",          -1),
    (r"\bdrop[ped]?\b",            -1),
    (r"\bfall[s]?\b",              -1),
    (r"\bweaker? (than expected)?\b", -1),
    (r"\bconcern[s]?\b",           -1),
]


def score_headline(headline: str) -> int:
    """
    Return an integer catalyst score for a headline.
    Positive = bullish catalyst, Negative = bearish catalyst.
    Typical range: -6 to +8.
    """
    h = headline.lower()
    return sum(w for pat, w in _CATALYST_RULES if re.search(pat, h))


# ---------------------------------------------------------------------------
# News fetching helpers
# ---------------------------------------------------------------------------

def _get_news_client():
    from alpaca.data.historical import StockHistoricalDataClient
    return StockHistoricalDataClient(
        api_key=Config.ALPACA_API_KEY,
        secret_key=Config.ALPACA_API_SECRET,
    )


def _fetch_articles(symbols: Optional[list[str]] = None, hours: int = 24, limit: int = 50):
    """
    Fetch news articles from Alpaca.
    If symbols is None, fetches broad market news.
    Returns list of article objects.
    """
    try:
        from alpaca.data.requests import NewsRequest
        client = _get_news_client()
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)

        kwargs = dict(start=start, end=end, limit=limit)
        if symbols:
            kwargs["symbols"] = symbols

        response = client.get_news(NewsRequest(**kwargs))
        return getattr(response, "news", [])
    except Exception as exc:
        logger.warning(f"News fetch failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_news_catalysts(
    hours: int = 24,
    min_score: int = 2,
    max_results: int = 10,
) -> list[tuple[str, int, str]]:
    """
    Scan recent market-wide news and return bullish catalyst symbols.

    Parameters
    ----------
    hours       : look-back window in hours (default 24)
    min_score   : minimum catalyst score to qualify (default 2)
    max_results : cap on returned symbols

    Returns
    -------
    List of (symbol, score, top_headline) sorted by score descending.
    Only includes symbols with at least one article above min_score.
    """
    articles = _fetch_articles(hours=hours, limit=100)
    if not articles:
        return []

    # Accumulate scores per symbol
    symbol_data: dict[str, dict] = {}
    for article in articles:
        s = score_headline(article.headline)
        for sym in (getattr(article, "symbols", None) or []):
            if not sym or len(sym) > 5:   # skip index symbols like ^VIX
                continue
            if sym not in symbol_data:
                symbol_data[sym] = {"total": 0, "count": 0, "top_score": -99, "top_headline": ""}
            symbol_data[sym]["total"] += s
            symbol_data[sym]["count"] += 1
            if s > symbol_data[sym]["top_score"]:
                symbol_data[sym]["top_score"] = s
                symbol_data[sym]["top_headline"] = article.headline

    # Filter and rank
    results = []
    for sym, d in symbol_data.items():
        avg = d["total"] / d["count"]
        if d["top_score"] >= min_score and avg > 0:
            results.append((sym, round(avg, 2), d["top_headline"]))

    results.sort(key=lambda x: x[1], reverse=True)

    if results:
        logger.info(
            f"News scanner found {len(results)} bullish catalysts "
            f"(top: {results[0][0]} score={results[0][1]})"
        )

    return results[:max_results]


def get_symbol_news_score(symbol: str, hours: int = 48) -> int:
    """
    Return the total catalyst score for a specific symbol over the last N hours.
    Used to boost / penalise existing technical signals.
    """
    articles = _fetch_articles(symbols=[symbol], hours=hours, limit=20)
    if not articles:
        return 0
    return sum(score_headline(a.headline) for a in articles)


def apply_news_boost(
    candidates: list[tuple[str, object, int]],
    boost_scale: float = 2.0,
    boost_cap: int = 15,
) -> list[tuple[str, object, int]]:
    """
    Re-rank a list of (symbol, signal, score) tuples by adding a news boost.

    boost_scale : multiply raw catalyst score by this to convert to signal pts
    boost_cap   : max points that news can add (or subtract) from technical score

    Returns the same list sorted by adjusted score, descending.
    """
    boosted = []
    for symbol, signal, tech_score in candidates:
        news_pts = get_symbol_news_score(symbol)
        bonus = max(-boost_cap, min(boost_cap, int(news_pts * boost_scale)))
        adjusted = tech_score + bonus
        if bonus != 0:
            logger.info(
                f"News boost {symbol}: tech={tech_score} news_pts={news_pts:+d} "
                f"bonus={bonus:+d} → adjusted={adjusted}"
            )
        boosted.append((symbol, signal, adjusted))

    boosted.sort(key=lambda x: x[2], reverse=True)
    return boosted
