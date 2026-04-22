"""
Pure intraday indicator calculations — no side effects.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Compute cumulative VWAP from session start (9:30 AM ET).

    VWAP = cumsum(typical_price * volume) / cumsum(volume)
    If the DataFrame already contains a 'vwap' column from Alpaca, prefer that
    only as a fallback (we compute our own for consistency across the session).
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].replace(0, np.nan).fillna(1)
    cum_pv = (typical * vol).cumsum()
    cum_v = vol.cumsum()
    return cum_pv / cum_v


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def opening_range(df: pd.DataFrame, minutes: int = 15) -> tuple[float, float]:
    """
    Compute the opening range high and low using the first N minutes of bars.

    Assumes the DataFrame is indexed by datetime (ET) and bars start at 9:30 AM.
    Returns (range_high, range_low).
    """
    if df.empty:
        return (0.0, 0.0)

    # Filter to bars between 9:30 and 9:30 + minutes
    try:
        start_time = df.index[0]
        end_time = start_time + pd.Timedelta(minutes=minutes)
        mask = (df.index >= start_time) & (df.index < end_time)
        orb_df = df.loc[mask]
    except Exception:
        orb_df = df.iloc[: max(1, minutes // 5)]

    if orb_df.empty:
        orb_df = df.iloc[:1]

    return (float(orb_df["high"].max()), float(orb_df["low"].min()))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period, min_periods=1).mean()


def add_intraday_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add derived columns to the intraday bar DataFrame:
      vwap, ema9, ema20, sma20, sma50, rsi, atr, atr_pct, session_high, session_low

    Returns a copy with the new columns appended.
    """
    if df.empty:
        return df
    out = df.copy()
    out["vwap"] = session_vwap(out)
    out["ema9"] = ema(out["close"], 9)
    out["ema20"] = ema(out["close"], 20)
    out["sma20"] = sma(out["close"], 20)
    out["sma50"] = sma(out["close"], 50)
    out["rsi"] = rsi(out["close"], 14)
    out["atr"] = _atr(out, 14)
    out["atr_pct"] = out["atr"] / out["close"].replace(0, np.nan)
    out["session_high"] = out["high"].cummax()
    out["session_low"] = out["low"].cummin()
    return out
