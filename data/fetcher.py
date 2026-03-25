"""
Market data fetcher using the Alpaca Data API.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

_client: Optional[StockHistoricalDataClient] = None


def _get_client() -> StockHistoricalDataClient:
    global _client
    if _client is None:
        _client = StockHistoricalDataClient(
            api_key=Config.ALPACA_API_KEY,
            secret_key=Config.ALPACA_API_SECRET,
        )
    return _client


def fetch_bars(
    symbol: str,
    timeframe: TimeFrame = TimeFrame.Day,
    lookback_days: int = 200,
) -> pd.DataFrame:
    """
    Fetch OHLCV bar data for a symbol.

    Returns a DataFrame with columns: open, high, low, close, volume.
    Index is a timezone-aware DatetimeIndex.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )

    try:
        bars = _get_client().get_stock_bars(request)
        df = bars.df

        if df.empty:
            logger.warning(f"No data returned for {symbol}")
            return pd.DataFrame()

        # If multi-symbol response, filter to this symbol
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")

        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()
        logger.debug(f"Fetched {len(df)} bars for {symbol}")
        return df

    except Exception as exc:
        logger.error(f"Failed to fetch bars for {symbol}: {exc}")
        return pd.DataFrame()


def fetch_latest_price(symbol: str) -> Optional[float]:
    """Return the most recent close price for a symbol."""
    df = fetch_bars(symbol, lookback_days=5)
    if df.empty:
        return None
    return float(df["close"].iloc[-1])


def fetch_batch(
    symbols: list[str],
    timeframe: TimeFrame = TimeFrame.Day,
    lookback_days: int = 200,
    delay: float = 0.1,
) -> dict[str, pd.DataFrame]:
    """
    Fetch bars for multiple symbols with a small inter-request delay
    to respect rate limits.
    """
    result: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = fetch_bars(sym, timeframe=timeframe, lookback_days=lookback_days)
        if not df.empty:
            result[sym] = df
        time.sleep(delay)
    logger.info(f"Fetched data for {len(result)}/{len(symbols)} symbols")
    return result
