"""
Technical indicators, implemented directly in pandas.

No third-party TA library — the previous app broke twice on pandas-ta / ta
version conflicts, and these five functions are all the strategy needs.
"""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP anchored to the start of the DataFrame (one session of bars)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum().replace(0, 1e-10)
    return (typical * df["volume"]).cumsum() / cum_vol


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def relative_volume(today_vols: pd.Series, prior_day_vols: list[pd.Series]) -> float | None:
    """
    RVOL vs the same time of day: today's cumulative volume so far divided by
    the average cumulative volume over the same number of bars on prior days.
    Returns None when there is no usable history.
    """
    n = len(today_vols)
    if n == 0 or not prior_day_vols:
        return None
    priors = [float(v.iloc[:n].sum()) for v in prior_day_vols if len(v) >= n]
    if not priors:
        return None
    baseline = sum(priors) / len(priors)
    if baseline <= 0:
        return None
    return float(today_vols.sum()) / baseline


def daily_atr_from_daily_bars(daily_df: pd.DataFrame, period: int = 14) -> float | None:
    """ATR over completed daily bars (caller must exclude today's partial bar)."""
    if len(daily_df) < period + 1:
        return None
    return float(atr(daily_df, period).iloc[-1])


def opening_range(df: pd.DataFrame, minutes: int) -> tuple[float, float]:
    """
    (high, low) of the first `minutes` of the session. `df` must contain a
    single session of intraday bars in time order.
    """
    if df.empty:
        return float("nan"), float("nan")
    start = df.index[0]
    window = df[df.index < start + pd.Timedelta(minutes=minutes)]
    return float(window["high"].max()), float(window["low"].min())
