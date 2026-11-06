"""
Market-regime and time-of-day classification — pure, independently measurable
functions reused by the optional selectivity filters (analysis/fib_filters.py)
and by the validation reporting (slice the results by regime / ToD bucket).

Everything is computed from bars already in hand (no look-ahead): callers pass
the window of bars up to and including the decision bar.
"""
from __future__ import annotations

import math

import pandas as pd


def ema(series: pd.Series, span: int) -> float:
    """Last EMA value over `series` (span in bars)."""
    if len(series) < 2:
        return float(series.iloc[-1])
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])


def trend_state(bars: pd.DataFrame, span: int = 20) -> str:
    """'up' | 'down' | 'flat' from price vs a short EMA and the EMA's slope."""
    close = bars["close"]
    if len(close) < span:
        return "flat"
    e_now = ema(close, span)
    e_prev = ema(close.iloc[:-max(1, span // 4)], span)
    price = float(close.iloc[-1])
    if price > e_now and e_now >= e_prev:
        return "up"
    if price < e_now and e_now <= e_prev:
        return "down"
    return "flat"


def realized_vol(bars: pd.DataFrame, lookback: int = 30,
                 annualize: float = 252 * 390) -> float:
    """Annualized realized vol from 1-min log returns over the last `lookback`
    bars (proxy for the intraday volatility regime)."""
    close = bars["close"].tail(lookback + 1)
    if len(close) < 5:
        return 0.0
    rets = (close / close.shift(1)).apply(lambda x: math.log(x) if x > 0 else 0.0).dropna()
    if len(rets) < 2:
        return 0.0
    return float(rets.std() * math.sqrt(annualize))


def vol_bucket(v: float) -> str:
    """Coarse volatility regime label."""
    if v < 0.10:
        return "low"
    if v < 0.20:
        return "normal"
    return "high"


def tod_bucket(ts: pd.Timestamp) -> str:
    """Time-of-day bucket (ET) — open drive / morning / lunch / afternoon / close."""
    t = ts.time()
    m = t.hour * 60 + t.minute
    if m < 10 * 60:
        return "open"          # 09:30-10:00
    if m < 11 * 60 + 30:
        return "morning"       # 10:00-11:30
    if m < 13 * 60 + 30:
        return "lunch"         # 11:30-13:30
    if m < 15 * 60:
        return "afternoon"     # 13:30-15:00
    return "close"             # 15:00-16:00


def in_window(ts: pd.Timestamp, window: str) -> bool:
    """Is `ts` (ET) inside an "HH:MM-HH:MM" window? Spans midnight if start>end.
    Empty window -> False."""
    window = (window or "").strip()
    if not window or "-" not in window:
        return False
    start_s, end_s = window.split("-", 1)
    sh, sm = map(int, start_s.split(":"))
    eh, em = map(int, end_s.split(":"))
    cur = ts.hour * 60 + ts.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end
