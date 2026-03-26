"""
Multi-Source Sentiment Engine
==============================
Aggregates four independent sentiment signals into a single composite score
that boosts or penalises the technical buy score for each candidate symbol.

Sources
-------
1. Options flow    (weight 30%) — put/call ratio + unusual volume
2. Analyst ratings (weight 30%) — upgrades, downgrades, price-target changes
3. Insider buying  (weight 25%) — SEC Form 4 via yfinance
4. Retail sentiment(weight 15%) — StockTwits bullish/bearish ratio (CONTRARIAN)

Scoring
-------
Each source returns an integer in [-3, +3].
The composite score is a weighted sum, normalised to [-10, +10].
Positive = bullish, Negative = bearish.

Caching
-------
To avoid hammering external APIs on every 10-minute scan, results are cached:
  - Options flow:     30 min
  - Analyst ratings:  4 hours
  - Insider activity: 4 hours
  - Retail sentiment: 15 min
Caches are per-process (reset on container restart).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# Cache: {symbol: {source: (score, fetched_epoch)}}
_cache: dict[str, dict[str, tuple[int, float]]] = {}

_TTL = {
    "options":  30 * 60,
    "analyst":  4 * 3600,
    "insider":  4 * 3600,
    "retail":   15 * 60,
}

_WEIGHTS = {
    "options": 0.30,
    "analyst": 0.30,
    "insider": 0.25,
    "retail":  0.15,
}


def _cached(symbol: str, source: str) -> Optional[int]:
    entry = _cache.get(symbol, {}).get(source)
    if entry and (time.time() - entry[1]) < _TTL[source]:
        return entry[0]
    return None


def _store(symbol: str, source: str, score: int) -> None:
    _cache.setdefault(symbol, {})[source] = (score, time.time())


# ---------------------------------------------------------------------------
# 1. Options flow
# ---------------------------------------------------------------------------

def _options_score(symbol: str) -> int:
    """
    Score based on put/call ratio and unusual call volume.

    P/C < 0.5   → +3  (strong call demand, bullish)
    P/C 0.5-0.7 → +2
    P/C 0.7-1.0 → +1
    P/C 1.0-1.2 →  0
    P/C 1.2-1.5 → -1
    P/C > 1.5   → -2  (heavy put buying, bearish)

    Bonus +1 if nearest-expiry call volume > 2× call open interest (unusual activity)
    """
    cached = _cached(symbol, "options")
    if cached is not None:
        return cached

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            _store(symbol, "options", 0)
            return 0

        # Use nearest expiration with data
        chain = ticker.option_chain(expirations[0])
        calls = chain.calls
        puts  = chain.puts

        call_vol = calls["volume"].fillna(0).sum()
        put_vol  = puts["volume"].fillna(0).sum()
        call_oi  = calls["openInterest"].fillna(0).sum()

        if call_vol + put_vol == 0:
            _store(symbol, "options", 0)
            return 0

        pc_ratio = put_vol / call_vol if call_vol > 0 else 2.0

        if pc_ratio < 0.5:
            score = 3
        elif pc_ratio < 0.7:
            score = 2
        elif pc_ratio < 1.0:
            score = 1
        elif pc_ratio < 1.2:
            score = 0
        elif pc_ratio < 1.5:
            score = -1
        else:
            score = -2

        # Unusual call activity bonus
        if call_oi > 0 and call_vol > call_oi * 2:
            score = min(3, score + 1)
            logger.info(f"{symbol} unusual call volume: {call_vol:,.0f} vs OI {call_oi:,.0f}")

        logger.info(f"{symbol} options: P/C={pc_ratio:.2f} → score={score:+d}")
        _store(symbol, "options", score)
        return score

    except Exception as exc:
        logger.debug(f"Options flow unavailable for {symbol}: {exc}")
        _store(symbol, "options", 0)
        return 0


# ---------------------------------------------------------------------------
# 2. Analyst ratings
# ---------------------------------------------------------------------------

_BULLISH_GRADES  = {"strong buy", "buy", "outperform", "overweight", "positive", "accumulate"}
_BEARISH_GRADES  = {"sell", "strong sell", "underperform", "underweight", "negative", "reduce"}
_UPGRADE_ACTIONS = {"up", "upgrade", "initiated", "reiterated"}
_DOWNGRADE_ACTIONS = {"down", "downgrade"}


def _analyst_score(symbol: str) -> int:
    """
    Score based on analyst upgrades/downgrades in the last 30 days.

    Each upgrade to Buy   → +2
    Each Buy rating init  → +1
    Each downgrade        → -2
    Each Sell rating      → -2
    Net capped at ±3
    """
    cached = _cached(symbol, "analyst")
    if cached is not None:
        return cached

    try:
        import yfinance as yf
        import pandas as pd
        ticker = yf.Ticker(symbol)

        # Try upgrades_downgrades first (newer yfinance), fall back to recommendations
        try:
            recs = ticker.upgrades_downgrades
        except Exception:
            recs = ticker.recommendations

        if recs is None or recs.empty:
            _store(symbol, "analyst", 0)
            return 0

        # Filter last 30 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        if recs.index.tz is None:
            recs.index = recs.index.tz_localize("UTC")
        recs = recs[recs.index >= cutoff]

        if recs.empty:
            _store(symbol, "analyst", 0)
            return 0

        score = 0
        for _, row in recs.iterrows():
            action = str(row.get("Action", row.get("action", ""))).lower()
            to_grade = str(row.get("To Grade", row.get("toGrade", ""))).lower()

            if action in _DOWNGRADE_ACTIONS or to_grade in _BEARISH_GRADES:
                score -= 2
            elif action in _UPGRADE_ACTIONS and to_grade in _BULLISH_GRADES:
                score += 2
            elif to_grade in _BULLISH_GRADES:
                score += 1

        score = max(-3, min(3, score))
        logger.info(f"{symbol} analyst: {len(recs)} ratings → score={score:+d}")
        _store(symbol, "analyst", score)
        return score

    except Exception as exc:
        logger.debug(f"Analyst data unavailable for {symbol}: {exc}")
        _store(symbol, "analyst", 0)
        return 0


# ---------------------------------------------------------------------------
# 3. Insider buying (SEC Form 4 via yfinance)
# ---------------------------------------------------------------------------

def _insider_score(symbol: str) -> int:
    """
    Score based on insider transactions in the last 90 days.

    Open-market purchase > $500K  → +3
    Open-market purchase > $100K  → +2
    Open-market purchase any size → +1
    Planned sale (10b5-1)         →  0  (ignore — pre-scheduled)
    Open-market sale              → -1  (weak signal — could be diversification)

    Net capped at ±3
    """
    cached = _cached(symbol, "insider")
    if cached is not None:
        return cached

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        txns = ticker.insider_transactions

        if txns is None or txns.empty:
            _store(symbol, "insider", 0)
            return 0

        # Filter last 90 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        if txns.index.tz is None:
            txns.index = txns.index.tz_localize("UTC")
        txns = txns[txns.index >= cutoff]

        if txns.empty:
            _store(symbol, "insider", 0)
            return 0

        score = 0
        for _, row in txns.iterrows():
            text = str(row.get("Text", row.get("transaction", ""))).lower()
            value = abs(float(row.get("Value", row.get("value", 0)) or 0))

            is_purchase = any(w in text for w in ("purchase", "buy", "acquisition", "acquired"))
            is_plan_sale = "10b5" in text or "plan" in text
            is_sale = any(w in text for w in ("sale", "sell", "disposed")) and not is_purchase

            if is_purchase:
                if value >= 500_000:
                    score += 3
                elif value >= 100_000:
                    score += 2
                else:
                    score += 1
                logger.info(f"{symbol} insider BUY: ${value:,.0f}")
            elif is_sale and not is_plan_sale:
                score -= 1

        score = max(-3, min(3, score))
        logger.info(f"{symbol} insider: {len(txns)} txns → score={score:+d}")
        _store(symbol, "insider", score)
        return score

    except Exception as exc:
        logger.debug(f"Insider data unavailable for {symbol}: {exc}")
        _store(symbol, "insider", 0)
        return 0


# ---------------------------------------------------------------------------
# 4. Retail sentiment — StockTwits (CONTRARIAN)
# ---------------------------------------------------------------------------

def _retail_score(symbol: str) -> int:
    """
    Score from StockTwits bullish/bearish message ratio — used CONTRARILY.

    When retail is euphoric (>70% bullish), smart money is often selling → bearish
    When retail is panicking (<30% bullish), oversold fear → opportunity → bullish

    Bullish% > 75% → -2  (too crowded, fade the retail euphoria)
    Bullish% 60-75% → -1
    Bullish% 40-60% →  0  (neutral)
    Bullish% 25-40% → +1
    Bullish% < 25%  → +2  (extreme fear = opportunity)

    Uses StockTwits public API (no auth required, ~200 req/hr limit)
    """
    cached = _cached(symbol, "retail")
    if cached is not None:
        return cached

    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
        resp = requests.get(url, timeout=5)
        if resp.status_code != 200:
            _store(symbol, "retail", 0)
            return 0

        messages = resp.json().get("messages", [])
        if not messages:
            _store(symbol, "retail", 0)
            return 0

        bullish = sum(
            1 for m in messages
            if (m.get("entities") or {}).get("sentiment", {}).get("basic") == "Bullish"
        )
        bearish = sum(
            1 for m in messages
            if (m.get("entities") or {}).get("sentiment", {}).get("basic") == "Bearish"
        )
        total = bullish + bearish
        if total == 0:
            _store(symbol, "retail", 0)
            return 0

        bull_pct = bullish / total

        # Contrarian scoring
        if bull_pct > 0.75:
            score = -2
        elif bull_pct > 0.60:
            score = -1
        elif bull_pct >= 0.40:
            score = 0
        elif bull_pct >= 0.25:
            score = 1
        else:
            score = 2

        logger.info(
            f"{symbol} retail: {bullish}B/{bearish}Be "
            f"({bull_pct:.0%} bullish) → contrarian score={score:+d}"
        )
        _store(symbol, "retail", score)
        return score

    except Exception as exc:
        logger.debug(f"StockTwits unavailable for {symbol}: {exc}")
        _store(symbol, "retail", 0)
        return 0


# ---------------------------------------------------------------------------
# Composite aggregator
# ---------------------------------------------------------------------------

def get_composite_sentiment(symbol: str) -> dict:
    """
    Fetch all four sentiment signals and return a composite result dict:

    {
        "symbol":    "AAPL",
        "composite": 4,          # weighted sum, range roughly -10 to +10
        "options":   2,
        "analyst":   3,
        "insider":   1,
        "retail":    0,
        "label":     "BULLISH"   # BULLISH | BEARISH | NEUTRAL
    }

    Only called for buy CANDIDATES — not the full watchlist — to limit API calls.
    """
    options  = _options_score(symbol)
    analyst  = _analyst_score(symbol)
    insider  = _insider_score(symbol)
    retail   = _retail_score(symbol)

    composite = (
        options  * _WEIGHTS["options"]  +
        analyst  * _WEIGHTS["analyst"]  +
        insider  * _WEIGHTS["insider"]  +
        retail   * _WEIGHTS["retail"]
    )
    composite = round(composite * (10 / 3), 1)   # scale to ≈ -10 to +10

    if composite >= 2:
        label = "BULLISH"
    elif composite <= -2:
        label = "BEARISH"
    else:
        label = "NEUTRAL"

    result = {
        "symbol":    symbol,
        "composite": composite,
        "options":   options,
        "analyst":   analyst,
        "insider":   insider,
        "retail":    retail,
        "label":     label,
    }

    logger.info(
        f"Sentiment [{symbol}] composite={composite:+.1f} ({label}) | "
        f"options={options:+d} analyst={analyst:+d} "
        f"insider={insider:+d} retail={retail:+d}"
    )
    return result


def is_sentiment_blocked(symbol: str, min_score: float = None) -> bool:
    """
    Return True when composite sentiment is too negative to trade.
    Replaces the simpler is_sentiment_negative() from market_filter.py.
    """
    from config import Config
    threshold = min_score if min_score is not None else Config.MIN_COMPOSITE_SENTIMENT
    result = get_composite_sentiment(symbol)
    if result["composite"] < threshold:
        logger.info(
            f"Sentiment block: {symbol} composite={result['composite']:+.1f} "
            f"< threshold {threshold}"
        )
        return True
    return False


def apply_sentiment_boost(
    candidates: list[tuple[str, object, int]],
    boost_scale: float = 2.0,
    boost_cap: int = 20,
) -> list[tuple[str, object, int]]:
    """
    Re-rank buy candidates by adding composite sentiment bonus to technical score.
    Replaces apply_news_boost() with the richer four-source composite.

    boost_scale : multiply composite score by this → signal points
    boost_cap   : max ± points sentiment can add/subtract from technical score
    """
    boosted = []
    for symbol, signal, tech_score in candidates:
        result = get_composite_sentiment(symbol)
        bonus = max(-boost_cap, min(boost_cap, int(result["composite"] * boost_scale)))
        adjusted = tech_score + bonus
        if bonus != 0:
            logger.info(
                f"Sentiment boost {symbol}: tech={tech_score} "
                f"sentiment={result['composite']:+.1f} bonus={bonus:+d} → {adjusted}"
            )
        boosted.append((symbol, signal, adjusted))

    boosted.sort(key=lambda x: x[2], reverse=True)
    return boosted
