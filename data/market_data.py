"""
Intraday stock market data via Alpaca's data API.

All timestamps are converted to US/Eastern because the whole strategy is
defined in exchange time (opening range, entry cutoff, flatten time).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from config import Config
from utils.logger import get_logger

logger = get_logger("market_data")

ET = ZoneInfo("America/New_York")

_stock_client = None


def _client():
    global _stock_client
    if _stock_client is None:
        from alpaca.data.historical import StockHistoricalDataClient

        _stock_client = StockHistoricalDataClient(
            Config.ALPACA_API_KEY, Config.ALPACA_API_SECRET
        )
    return _stock_client


def get_intraday_bars(
    symbol: str,
    minutes: int | None = None,
    lookback_days: int = 1,
) -> pd.DataFrame:
    """
    Fetch intraday bars for `symbol` covering today (and `lookback_days - 1`
    prior days). Returns a DataFrame indexed by ET timestamp with columns
    open/high/low/close/volume, restricted to regular trading hours.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    minutes = minutes or Config.BAR_MINUTES
    start = datetime.now(timezone.utc) - timedelta(days=max(lookback_days, 1) + 1)

    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(minutes, TimeFrameUnit.Minute),
        start=start,
    )
    try:
        bars = _client().get_stock_bars(req)
    except Exception as exc:
        logger.error(f"Bar fetch failed for {symbol}: {exc}")
        return pd.DataFrame()

    df = bars.df
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level="symbol")

    df = df.tz_convert(ET)
    # Regular trading hours only — pre/post-market bars pollute VWAP and the
    # opening range.
    df = df.between_time("09:30", "16:00")
    return df[["open", "high", "low", "close", "volume"]]


def get_today_bars(symbol: str, minutes: int | None = None) -> pd.DataFrame:
    """Bars for the current ET session only."""
    df = get_intraday_bars(symbol, minutes=minutes, lookback_days=1)
    if df.empty:
        return df
    today = datetime.now(ET).date()
    return df[df.index.date == today]


def get_latest_price(symbol: str) -> float | None:
    from alpaca.data.requests import StockLatestTradeRequest

    try:
        req = StockLatestTradeRequest(symbol_or_symbols=symbol)
        trade = _client().get_stock_latest_trade(req)[symbol]
        return float(trade.price)
    except Exception as exc:
        logger.error(f"Latest price fetch failed for {symbol}: {exc}")
        return None
