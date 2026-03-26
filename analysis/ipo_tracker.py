"""
IPO Tracker — discovers, monitors, and trades newly listed stocks.

How it works
------------
1. Discovery  : Scans Alpaca news every cycle for IPO/listing keywords.
                Extracts the ticker symbol and records the listing date.

2. Maturation : New IPOs are quarantined for IPO_MIN_DAYS (default 5 trading
                days) to let the opening volatility settle and accumulate
                enough price history for indicators.

3. Signals    : Once mature, uses shortened indicator periods that fit the
                available history instead of the standard 50-day EMA.
                Strategy: momentum (price > VWAP proxy), volume spike,
                and a simple higher-highs structure.

4. Risk       : IPO positions are sized at IPO_POSITION_SCALE of normal
                (default 0.5×) and use a wider stop (IPO_STOP_LOSS_PCT).

Persistence   : Tracked IPOs are saved to data/ipo_watchlist.json so they
                survive container restarts.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

_STORE = Path(__file__).parent.parent / "data" / "ipo_watchlist.json"

# Keywords that indicate an IPO / first listing in a headline
_IPO_PATTERNS = [
    r"\bipo\b",
    r"\binitial public offering\b",
    r"\bbegins? trading\b",
    r"\bstarts? trading\b",
    r"\bmakes? (?:its )?(?:stock market |market )?debut\b",
    r"\blisted on (?:the )?(?:nyse|nasdaq|NYSE|NASDAQ)\b",
    r"\bgoes? public\b",
    r"\bpriced its ipo\b",
    r"\bfirst day of trading\b",
    r"\bshares begin trading\b",
]

_IPO_RE = re.compile("|".join(_IPO_PATTERNS), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load() -> dict[str, dict]:
    """Load the persisted IPO watchlist."""
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    if _STORE.exists():
        try:
            return json.loads(_STORE.read_text())
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    _STORE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def scan_ipo_news(hours: int = 48) -> list[tuple[str, str]]:
    """
    Scan recent news for IPO announcements.
    Returns list of (symbol, headline) for newly detected IPOs.
    """
    try:
        from data.news import fetch_news
        articles = fetch_news(hours=hours, limit=50)
    except Exception as exc:
        logger.warning(f"IPO news scan failed: {exc}")
        return []

    found = []
    for article in articles:
        if _IPO_RE.search(article.headline):
            for sym in (getattr(article, "symbols", None) or []):
                if sym and len(sym) <= 5 and sym.isalpha():
                    found.append((sym, article.headline))
                    logger.info(f"IPO detected: {sym} — '{article.headline}'")

    return found


def register_new_ipos(hours: int = 48) -> list[str]:
    """
    Scan news, register any newly detected IPOs, and return
    the list of newly added symbols.
    """
    watchlist = _load()
    new_symbols = []

    for sym, headline in scan_ipo_news(hours=hours):
        if sym not in watchlist and sym not in Config.WATCHLIST:
            watchlist[sym] = {
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "headline": headline,
                "status": "quarantine",   # quarantine → active → graduated/removed
            }
            new_symbols.append(sym)
            logger.info(f"Registered new IPO: {sym}")

    if new_symbols:
        _save(watchlist)

    return new_symbols


# ---------------------------------------------------------------------------
# Maturation check
# ---------------------------------------------------------------------------

def get_tradeable_ipos() -> list[str]:
    """
    Return IPO symbols that have passed the quarantine period and
    have not yet been graduated to the main watchlist or removed.
    """
    watchlist = _load()
    now = datetime.now(timezone.utc)
    tradeable = []

    for sym, info in watchlist.items():
        if info.get("status") != "quarantine":
            continue
        detected = datetime.fromisoformat(info["detected_at"])
        days_old = (now - detected).days
        if days_old >= Config.IPO_MIN_DAYS:
            tradeable.append(sym)

    return tradeable


def graduate_ipo(symbol: str) -> None:
    """Mark an IPO as graduated (added to main watchlist or enough history)."""
    watchlist = _load()
    if symbol in watchlist:
        watchlist[symbol]["status"] = "graduated"
        _save(watchlist)
        logger.info(f"IPO graduated to main watchlist: {symbol}")


def remove_ipo(symbol: str, reason: str = "") -> None:
    """Remove a failed/delisted IPO from tracking."""
    watchlist = _load()
    if symbol in watchlist:
        watchlist[symbol]["status"] = "removed"
        watchlist[symbol]["remove_reason"] = reason
        _save(watchlist)
        logger.info(f"IPO removed: {symbol} ({reason})")


# ---------------------------------------------------------------------------
# IPO-specific signal
# ---------------------------------------------------------------------------

def generate_ipo_signal(df: pd.DataFrame) -> tuple[str, int]:
    """
    Lightweight signal for stocks with limited price history (< 50 days).

    Strategy:
      - Uptrend: price above short EMA (5-day)
      - Momentum: price gaining over last 3 bars
      - Volume: today's volume > 1.5× the short average (institutional buying)
      - Not over-extended: price < 1.20× its 10-day average (avoid chasing spikes)

    Returns ('BUY'|'SELL'|'HOLD', score 0-100).
    """
    if len(df) < 6:
        return "HOLD", 0

    df = df.copy()
    df["ema5"] = df["close"].ewm(span=5).mean()
    df["vol_avg"] = df["volume"].rolling(min(10, len(df))).mean()
    df["close_avg10"] = df["close"].rolling(min(10, len(df))).mean()

    latest = df.iloc[-1]
    prev3 = df.iloc[-4:-1]

    score = 0

    # 1. Price above 5-day EMA — short uptrend (25 pts)
    if latest["close"] > latest["ema5"]:
        score += 25

    # 2. Three consecutive higher closes — momentum (25 pts)
    if all(prev3["close"].diff().dropna() > 0):
        score += 25

    # 3. Volume spike — institutional buying (25 pts)
    if latest["vol_avg"] > 0 and latest["volume"] > latest["vol_avg"] * 1.5:
        score += 25

    # 4. Not over-extended — avoid buying a spike (25 pts if within range)
    if latest["close_avg10"] > 0 and latest["close"] < latest["close_avg10"] * 1.2:
        score += 25

    # Penalise if price is collapsing below EMA (bearish)
    if latest["close"] < latest["ema5"] * 0.97:
        return "SELL", 60

    if score >= 60:
        return "BUY", score
    return "HOLD", score


# ---------------------------------------------------------------------------
# IPO position sizing (more conservative)
# ---------------------------------------------------------------------------

def ipo_position_size(
    equity: float,
    price: float,
) -> tuple[int, float, float]:
    """
    Conservative sizing for IPO entries.
    Uses IPO_POSITION_SCALE × normal max position, wider stop.
    Returns (shares, stop_loss_price, take_profit_price).
    """
    max_cost = equity * Config.MAX_POSITION_PCT * Config.IPO_POSITION_SCALE
    shares = max(1, int(max_cost / price))

    stop_loss = price * (1 - Config.IPO_STOP_LOSS_PCT)
    take_profit = price * (1 + Config.IPO_STOP_LOSS_PCT * 3)  # 3:1 R:R

    return shares, round(stop_loss, 2), round(take_profit, 2)
