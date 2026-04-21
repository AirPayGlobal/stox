"""
Intraday bar fetching using the StoxDaily Alpaca data client.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pandas as pd

from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from intraday.client import get_data_client
from utils.logger import get_logger

logger = get_logger("intraday.data")

_ET = timezone(timedelta(hours=-4))  # EDT offset; close enough for market hours


def _et_now() -> datetime:
    return datetime.now(tz=_ET)


def fetch_bars(
    symbol: str,
    timeframe_minutes: int = 5,
    lookback_bars: int = 100,
) -> pd.DataFrame:
    """
    Fetch recent intraday bars for a single symbol.

    Returns a DataFrame with columns: open, high, low, close, volume, vwap
    indexed by datetime (ET timezone). Returns an empty DataFrame on failure.
    """
    try:
        client = get_data_client()
        tf = TimeFrame(timeframe_minutes, TimeFrameUnit.Minute)
        # Lookback: enough calendar time to cover lookback_bars (add buffer for weekends/holidays)
        lookback_seconds = lookback_bars * timeframe_minutes * 60 * 2
        start = datetime.now(tz=timezone.utc) - timedelta(seconds=lookback_seconds)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
        )
        bars = client.get_stock_bars(req)
        df = bars.df
        if df is None or df.empty:
            return pd.DataFrame()

        # If multi-level columns from batch call, select symbol
        if isinstance(df.index, pd.MultiIndex):
            if symbol in df.index.get_level_values(0):
                df = df.xs(symbol, level=0)
            else:
                return pd.DataFrame()

        df = df.rename(columns=str.lower)

        # Ensure required columns
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                df[col] = float("nan")

        if "vwap" not in df.columns:
            df["vwap"] = float("nan")

        # Convert index to ET timezone
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert("America/New_York")
        else:
            df.index = df.index.tz_convert("America/New_York")

        df = df[["open", "high", "low", "close", "volume", "vwap"]].copy()
        df = df.tail(lookback_bars)
        return df
    except Exception as exc:
        logger.warning("fetch_bars(%s) failed: %s", symbol, exc)
        return pd.DataFrame()


def fetch_bars_batch(
    symbols: list[str],
    timeframe_minutes: int = 5,
    lookback_bars: int = 100,
) -> dict[str, pd.DataFrame]:
    """
    Fetch bars for multiple symbols in a single API call.
    Returns a dict of symbol -> DataFrame.
    """
    result: dict[str, pd.DataFrame] = {}
    if not symbols:
        return result
    try:
        client = get_data_client()
        tf = TimeFrame(timeframe_minutes, TimeFrameUnit.Minute)
        lookback_seconds = lookback_bars * timeframe_minutes * 60 * 2
        start = datetime.now(tz=timezone.utc) - timedelta(seconds=lookback_seconds)
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=tf,
            start=start,
        )
        bars = client.get_stock_bars(req)
        df_all = bars.df
        if df_all is None or df_all.empty:
            return result

        df_all = df_all.rename(columns=str.lower)

        for sym in symbols:
            try:
                if isinstance(df_all.index, pd.MultiIndex):
                    if sym not in df_all.index.get_level_values(0):
                        continue
                    df = df_all.xs(sym, level=0).copy()
                else:
                    df = df_all.copy()

                for col in ("open", "high", "low", "close", "volume"):
                    if col not in df.columns:
                        df[col] = float("nan")
                if "vwap" not in df.columns:
                    df["vwap"] = float("nan")

                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC").tz_convert("America/New_York")
                else:
                    df.index = df.index.tz_convert("America/New_York")

                df = df[["open", "high", "low", "close", "volume", "vwap"]].copy()
                df = df.tail(lookback_bars)
                result[sym] = df
            except Exception as exc:
                logger.warning("fetch_bars_batch: failed to process %s: %s", sym, exc)
    except Exception as exc:
        logger.warning("fetch_bars_batch failed: %s", exc)
    return result


def fetch_premarket_high(symbol: str) -> float:
    """Return the highest price seen in pre-market (4 AM - 9:30 AM ET) today."""
    try:
        client = get_data_client()
        now_et = _et_now()
        today_open = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        end = min(now_et, market_open)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(1, TimeFrameUnit.Minute),
            start=today_open.astimezone(timezone.utc),
            end=end.astimezone(timezone.utc),
        )
        bars = client.get_stock_bars(req)
        df = bars.df
        if df is None or df.empty:
            return 0.0
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)
        return float(df["high"].max())
    except Exception as exc:
        logger.warning("fetch_premarket_high(%s) failed: %s", symbol, exc)
        return 0.0


def fetch_premarket_low(symbol: str) -> float:
    """Return the lowest price seen in pre-market today."""
    try:
        client = get_data_client()
        now_et = _et_now()
        today_open = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        end = min(now_et, market_open)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(1, TimeFrameUnit.Minute),
            start=today_open.astimezone(timezone.utc),
            end=end.astimezone(timezone.utc),
        )
        bars = client.get_stock_bars(req)
        df = bars.df
        if df is None or df.empty:
            return float("inf")
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)
        return float(df["low"].min())
    except Exception as exc:
        logger.warning("fetch_premarket_low(%s) failed: %s", symbol, exc)
        return float("inf")


def fetch_premarket_close(symbol: str) -> float:
    """Return the last pre-market price (close of most recent pre-market bar)."""
    try:
        client = get_data_client()
        now_et = _et_now()
        today_open = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        end = min(now_et, market_open)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(1, TimeFrameUnit.Minute),
            start=today_open.astimezone(timezone.utc),
            end=end.astimezone(timezone.utc),
        )
        bars = client.get_stock_bars(req)
        df = bars.df
        if df is None or df.empty:
            return 0.0
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)
        return float(df["close"].iloc[-1])
    except Exception as exc:
        logger.warning("fetch_premarket_close(%s) failed: %s", symbol, exc)
        return 0.0


def get_prev_close(symbol: str) -> float:
    """Return the previous regular-session closing price."""
    try:
        client = get_data_client()
        now_et = _et_now()
        start = now_et - timedelta(days=5)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(1, TimeFrameUnit.Day),
            start=start.astimezone(timezone.utc),
        )
        bars = client.get_stock_bars(req)
        df = bars.df
        if df is None or df.empty:
            return 0.0
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)
        # The last bar is today (if after session) or yesterday
        if len(df) >= 2:
            return float(df["close"].iloc[-2])
        return float(df["close"].iloc[-1])
    except Exception as exc:
        logger.warning("get_prev_close(%s) failed: %s", symbol, exc)
        return 0.0
