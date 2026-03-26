"""
Earnings calendar: blackout window around earnings reports.

Institutional traders reduce or exit positions before earnings because
binary outcomes (beat/miss) bypass all technical analysis.

Rules applied
-------------
- No new BUY entries within EARNINGS_BLACKOUT_DAYS of the next report
- Existing positions receive a warning log so the user can decide to close manually
  (bot does not force-close existing positions — that's a user call)

Data source: yfinance earnings calendar (free, no API key needed)
Falls back gracefully: if earnings data is unavailable, does NOT block the trade.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# Simple in-process cache: {symbol: (days_to_earnings, fetched_at_epoch)}
_cache: dict[str, tuple[Optional[int], float]] = {}
_CACHE_TTL_SECONDS = 3600  # refresh once per hour


def days_to_earnings(symbol: str) -> Optional[int]:
    """
    Return calendar days until the next scheduled earnings date.
    Returns None if unknown or if the date has already passed.
    Uses a 1-hour cache to avoid hammering yfinance.
    """
    import time
    now_epoch = time.time()

    if symbol in _cache:
        cached_days, fetched_at = _cache[symbol]
        if now_epoch - fetched_at < _CACHE_TTL_SECONDS:
            return cached_days

    result = _fetch_days(symbol)
    _cache[symbol] = (result, now_epoch)
    return result


def _fetch_days(symbol: str) -> Optional[int]:
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar

        if cal is None:
            return None

        # yfinance ≥ 0.2 returns a dict; older versions return a DataFrame
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or cal.get("earningsDate") or []
            if not dates:
                return None
            raw = dates[0] if isinstance(dates, list) else dates
        else:
            # DataFrame format (older yfinance)
            try:
                raw = cal.loc["Earnings Date"].iloc[0]
            except (KeyError, IndexError):
                return None

        import pandas as pd
        if pd.isna(raw):
            return None

        # Normalise to timezone-aware datetime
        if hasattr(raw, "tzinfo"):
            earnings_dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        else:
            earnings_dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)

        days = (earnings_dt - datetime.now(timezone.utc)).days
        return days if days >= 0 else None

    except Exception as exc:
        logger.debug(f"Earnings calendar unavailable for {symbol}: {exc}")
        return None


def is_earnings_blackout(symbol: str, days_before: int = None) -> bool:
    """
    Return True when the stock is in the pre-earnings blackout window.
    If True, the bot skips the BUY entry for this symbol.

    days_before defaults to Config.EARNINGS_BLACKOUT_DAYS.
    Fails open (returns False) when calendar data is unavailable.
    """
    from config import Config
    window = days_before if days_before is not None else Config.EARNINGS_BLACKOUT_DAYS

    days = days_to_earnings(symbol)
    if days is None:
        return False

    if 0 <= days <= window:
        logger.info(
            f"Earnings blackout: {symbol} reports in {days} day(s) "
            f"(window={window}) — skipping entry"
        )
        return True

    if days <= window + 2:
        logger.debug(f"{symbol} earnings in {days} days — approaching blackout window")

    return False


def warn_open_positions_near_earnings(open_symbols: list[str], days_before: int = 3) -> None:
    """
    Log warnings for any open positions with earnings within `days_before` days.
    Does not force-close — user decides.
    """
    for symbol in open_symbols:
        days = days_to_earnings(symbol)
        if days is not None and 0 <= days <= days_before:
            logger.warning(
                f"EARNINGS ALERT: {symbol} reports in {days} day(s). "
                f"Consider closing or reducing position before the report."
            )
