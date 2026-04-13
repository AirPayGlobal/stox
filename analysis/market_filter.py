"""
Market-level filters applied before any new BUY order is placed.

Filters:
  1. VIX filter  — skip all new entries when the CBOE Volatility Index > threshold
  2. News sentiment — score recent headlines for a symbol; skip if too negative
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# VIX filter
# ---------------------------------------------------------------------------

_POSITIVE = {
    "beat", "beats", "record", "profit", "surge", "upgrade", "raised",
    "growth", "strong", "outperform", "exceeds", "above", "bullish",
    "buy", "positive", "recovery", "rebound", "rally",
}
_NEGATIVE = {
    "miss", "misses", "loss", "losses", "cut", "cuts", "warn", "warning",
    "downgrade", "below", "weak", "underperform", "tariff", "tariffs",
    "sanction", "sanctions", "war", "crisis", "crash", "decline", "drop",
    "fear", "recession", "default", "bankruptcy", "layoff", "layoffs",
    "investigation", "fraud", "lawsuit", "fine", "penalty", "trump",
}


_vix_cache: dict = {"value": None, "ts": 0.0}
_VIX_CACHE_TTL = 300  # 5 minutes — VIX doesn't move meaningfully in seconds


def get_vix() -> float:
    """
    Return the latest VIX (CBOE Volatility Index) level.
    Uses yfinance to pull ^VIX. Result cached for 5 minutes to avoid
    hammering yfinance on every dashboard poll. Falls back to 20.0 on error.
    """
    import time
    now = time.monotonic()
    if _vix_cache["value"] is not None and (now - _vix_cache["ts"]) < _VIX_CACHE_TTL:
        return _vix_cache["value"]
    try:
        import yfinance as yf
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="2d")
        if hist.empty:
            logger.warning("VIX data empty — assuming neutral (20.0)")
            return 20.0
        vix = float(hist["Close"].iloc[-1])
        _vix_cache["value"] = vix
        _vix_cache["ts"] = now
        logger.info(f"VIX = {vix:.2f}")
        return vix
    except Exception as exc:
        logger.warning(f"Could not fetch VIX ({exc}) — assuming neutral (20.0)")
        return 20.0


def is_vix_too_high(threshold: float = None) -> bool:
    """
    Return True (block new entries) when VIX exceeds the configured threshold.
    """
    threshold = threshold or Config.VIX_THRESHOLD
    vix = get_vix()
    if vix > threshold:
        logger.warning(
            f"VIX {vix:.1f} > {threshold} — skipping all new BUY entries (high fear)"
        )
        return True
    return False


# ---------------------------------------------------------------------------
# News sentiment filter (Alpaca News API)
# ---------------------------------------------------------------------------

def _score_headline(headline: str) -> float:
    """
    Simple bag-of-words sentiment score.
    Returns +1 for each positive word, -1 for each negative word.
    Normalised to [-1, +1] range.
    """
    words = set(re.findall(r"[a-z]+", headline.lower()))
    pos = len(words & _POSITIVE)
    neg = len(words & _NEGATIVE)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


def get_news_sentiment(symbol: str, days: int = 3) -> float:
    """
    Fetch recent news headlines for *symbol* via the Alpaca News API and
    return an average sentiment score in [-1, +1].

    Positive  → good news for the stock
    Negative  → bad news / macro headwinds
    0.0       → neutral or no news

    Falls back to 0.0 on any API error.
    """
    try:
        from data.news import fetch_news
        articles = fetch_news(symbols=[symbol], hours=days * 24, limit=20)
        if not articles:
            logger.debug(f"No recent news for {symbol}")
            return 0.0

        scores = [_score_headline(a.headline) for a in articles]
        avg = sum(scores) / len(scores)
        logger.info(
            f"News sentiment for {symbol}: {avg:+.2f} "
            f"({len(articles)} articles, last {days} days)"
        )
        return avg

    except Exception as exc:
        logger.warning(f"News sentiment unavailable for {symbol} ({exc}) — skipping filter")
        return 0.0


def is_sentiment_negative(symbol: str) -> bool:
    """
    Return True (block BUY) when recent news sentiment is below the
    configured minimum threshold.
    """
    score = get_news_sentiment(symbol)
    if score < Config.MIN_SENTIMENT_SCORE:
        logger.info(
            f"Skipping {symbol} — sentiment {score:+.2f} < "
            f"threshold {Config.MIN_SENTIMENT_SCORE}"
        )
        return True
    return False
