"""Higher-timeframe candle construction from intraday bars."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

_OHLCV = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def resample_bars(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """
    Aggregate intraday bars into `minutes`-minute candles aligned to the
    09:30 ET session open (so 60-min candles are 09:30, 10:30, … not 09:00).
    """
    if df.empty:
        return df
    return (
        df.resample(f"{minutes}min", origin="start_day", offset="9h30min")
        .agg(_OHLCV)
        .dropna()
    )


def completed_bars(htf_df: pd.DataFrame, minutes: int, asof: datetime) -> pd.DataFrame:
    """
    Drop the still-forming candle: keep only candles whose window has fully
    elapsed as of `asof`. Bar timestamps are window-start times.
    """
    if htf_df.empty:
        return htf_df
    cutoff = asof - timedelta(minutes=minutes)
    return htf_df[htf_df.index <= cutoff]
