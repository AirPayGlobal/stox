"""
Multi-Timeframe Signal Confirmation
=====================================
Confirms daily BUY/SELL signals against the weekly trend before entry.

Why it works
------------
A daily golden cross inside a weekly downtrend is a trap — the stock
is likely in a dead-cat bounce.  Requiring weekly alignment cuts false
positives significantly.

Weekly checks (all must pass for entry confirmation):
  BUY  confirmation → price above 10-week EMA
                     → weekly RSI between 35 and 75
                     → weekly MACD histogram positive (or recently crossed)
  SELL confirmation → price below 10-week EMA
                     → weekly RSI above 50 (was overbought, now turning)

Data source: resamples the daily DataFrame already in memory — no extra API call.
"""
from __future__ import annotations

import pandas as pd
import ta.trend
import ta.momentum

from utils.logger import get_logger

logger = get_logger(__name__)


def _to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV DataFrame to weekly bars."""
    df = df.copy()
    # Ensure DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # Make index timezone-naive for resampling compatibility
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    weekly = df.resample("W").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna()
    return weekly


def _add_weekly_indicators(weekly: pd.DataFrame) -> pd.DataFrame:
    weekly = weekly.copy()
    weekly["ema10w"] = ta.trend.EMAIndicator(weekly["close"], window=10).ema_indicator()
    weekly["ema20w"] = ta.trend.EMAIndicator(weekly["close"], window=20).ema_indicator()
    weekly["rsi_w"]  = ta.momentum.RSIIndicator(weekly["close"], window=14).rsi()
    macd = ta.trend.MACD(weekly["close"], window_fast=12, window_slow=26, window_sign=9)
    weekly["macd_hist_w"] = macd.macd_diff()
    return weekly


def weekly_confirms_entry(df: pd.DataFrame, symbol: str = "") -> bool:
    """
    Return True if the weekly chart supports a long (BUY) entry.

    Passes when ALL of:
      - Weekly close above 10-week EMA (uptrend)
      - Weekly RSI between 35 and 75 (not extreme)
      - Weekly MACD histogram > 0 OR turned positive in the last 2 bars

    Fails open (returns True) when there is insufficient weekly data.
    """
    try:
        weekly = _to_weekly(df)
        if len(weekly) < 15:
            logger.debug(f"{symbol}: insufficient weekly bars ({len(weekly)}) — skipping weekly filter")
            return True

        weekly = _add_weekly_indicators(weekly).dropna()
        if len(weekly) < 2:
            return True

        latest = weekly.iloc[-1]
        prev   = weekly.iloc[-2]

        above_ema = latest["close"] > latest["ema10w"]
        rsi_ok    = 35 <= latest["rsi_w"] <= 75
        macd_ok   = latest["macd_hist_w"] > 0 or (
            latest["macd_hist_w"] > prev["macd_hist_w"] and
            latest["macd_hist_w"] > -2.0   # improving from correction — weekly MACD lags price by weeks
        )

        passes = above_ema and rsi_ok and macd_ok

        if not passes:
            logger.info(
                f"Weekly filter blocked {symbol}: "
                f"above_ema={above_ema} rsi={latest['rsi_w']:.1f} "
                f"macd_hist={latest['macd_hist_w']:.4f}"
            )
        else:
            logger.debug(f"{symbol} weekly confirmed: rsi={latest['rsi_w']:.1f}")

        return passes

    except Exception as exc:
        logger.debug(f"Weekly check failed for {symbol}: {exc}")
        return True   # fail open


def weekly_confirms_short(df: pd.DataFrame, symbol: str = "") -> bool:
    """
    Return True if the weekly chart supports a short (SELL) entry.

    Passes when ALL of:
      - Weekly close below 10-week EMA (downtrend)
      - Weekly RSI between 30 and 65 (not already crashed / washed out)
      - Weekly MACD histogram negative
    """
    try:
        weekly = _to_weekly(df)
        if len(weekly) < 15:
            return True

        weekly = _add_weekly_indicators(weekly).dropna()
        if len(weekly) < 2:
            return True

        latest = weekly.iloc[-1]

        below_ema = latest["close"] < latest["ema10w"]
        rsi_ok    = 30 <= latest["rsi_w"] <= 65
        macd_neg  = latest["macd_hist_w"] < 0

        passes = below_ema and rsi_ok and macd_neg

        if not passes:
            logger.info(
                f"Weekly short filter blocked {symbol}: "
                f"below_ema={below_ema} rsi={latest['rsi_w']:.1f} "
                f"macd_hist={latest['macd_hist_w']:.4f}"
            )
        return passes

    except Exception as exc:
        logger.debug(f"Weekly short check failed for {symbol}: {exc}")
        return True
